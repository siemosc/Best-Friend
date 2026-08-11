"""Доставка outbound-событий стрима в Telegram — модель вывода A*.

Draft — живое превью «печатается» (rich-черновик под троттлингом коалесера,
эфемерен, гаснет только новым сообщением); anchor — обычное сообщение с
прогресс-логом (edit по ходу работы); финал — всегда новый send: rich-пузырь
[свёрнутый лог в `<details>` + ответ], draft гаснет, anchor удаляется.

Разметку рендерит сам Telegram (Rich Messages, Bot API 10.1): наружу уходит
markdown как есть. Telegram отверг разметку — тот же текст досылается plain-ом,
поэтому ответ доходит и на «грязном» markdown модели. Любой сбой доставки
гасится здесь: `dispatch_stream_event` не пробрасывает ошибки Telegram наружу.
"""

import contextlib
from functools import partial

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.types import InputRichMessage, ReplyParameters
from loguru import logger

from bestfiend.contracts.events import (
    AnswerDelta,
    AnswerFinal,
    AnswerReset,
    ProgressStep,
)
from bestfiend.telegram.artifact_delivery import ArtifactDelivery
from bestfiend.telegram.draft_stream import DraftStreamCoalescer
from bestfiend.telegram.formatters import (
    RICH_MESSAGE_CHAR_LIMIT,
    THINKING_DRAFT_MARKDOWN,
    chunk_by_utf16_limit,
    compose_final_markdown,
    compose_plain_fallback,
    format_progress,
    split_rich_markdown,
    tail_by_utf16_limit,
)


_BLOCK_SEPARATOR = "\n\n"

# Обвязка лога съела половину лимита rich-сообщения — ответу места не остаётся,
# поэтому лог уезжает отдельным сообщением, а финал идёт без `<details>`.
_OVERSIZED_LOG_RESERVE = RICH_MESSAGE_CHAR_LIMIT // 2


class OutboundDelivery:
    """Маршрутизация outbound-событий одного запроса в Telegram-действия.

    Держит per-request стейт стрима: anchor-сообщения, накопленные
    прогресс-шаги, буферы дельт, коалесеры черновика и множество
    финализированных request_id (гасит поздние события).
    """

    def __init__(self, *, bot: Bot, artifact_delivery: ArtifactDelivery) -> None:
        self._bot = bot
        self._artifact_delivery = artifact_delivery
        # Anchor — обычное сообщение с прогресс-логом: edit во время работы,
        # delete в финале (лог переезжает в свёрнутый блок ответа).
        self._anchor_messages: dict[str, int] = {}
        self._progress_steps: dict[str, list[str]] = {}
        # Финализированные request_id — гасят поздние ProgressStep/AnswerDelta.
        self._finalized: set[str] = set()
        # In-process consumer: накопительный буфер дельт per request_id.
        self._stream_buffers: dict[str, str] = {}
        # Троттлинг черновика: один коалесер на запрос, фоновых задач у него нет.
        self._draft_coalescers: dict[str, DraftStreamCoalescer] = {}
        # Запросы, где Telegram отверг rich-черновик: дальше только plain-черновик.
        self._plain_draft_requests: set[str] = set()

    @staticmethod
    def draft_id(request_id: str) -> int:
        """Стабильный draft_id для request_id — «Думаю…» и стрим бьют в один черновик."""
        return hash(request_id) & 0x7FFFFFFF or 1

    async def send_thinking_draft(self, *, chat_id: int, request_id: str) -> None:
        """Ставит в черновик «думаю» — сигнал «взялся» ещё до первого события графа.

        Черновики живут только в приватных чатах, поэтому сбой здесь штатен:
        сначала деградация до plain, затем no-op.
        """
        with contextlib.suppress(TelegramAPIError):
            await self._push_draft(
                chat_id=chat_id,
                request_id=request_id,
                markdown=THINKING_DRAFT_MARKDOWN,
                plain_text="",
            )

    def cleanup_request_state(self, request_id: str) -> None:
        """Снимает весь per-request стейт (идемпотентно) — защита от утечки при аварии."""
        self._anchor_messages.pop(request_id, None)
        self._progress_steps.pop(request_id, None)
        self._finalized.discard(request_id)
        self._stream_buffers.pop(request_id, None)
        self._draft_coalescers.pop(request_id, None)
        self._plain_draft_requests.discard(request_id)

    async def dispatch_stream_event(
        self,
        *,
        request_id: str,
        chat_id: int,
        reply_to_message_id: int | None,
        outbound: AnswerDelta | ProgressStep | AnswerFinal | AnswerReset,
    ) -> None:
        """Маршрутизирует один outbound event в Telegram-action, гася сбои доставки."""
        try:
            await self._route_stream_event(
                request_id=request_id,
                chat_id=chat_id,
                reply_to_message_id=reply_to_message_id,
                outbound=outbound,
            )
        except TelegramAPIError as exc:
            # Канал не имеет права ронять потребителя стрима: ответ уже потерян,
            # но граф и остальные события запроса продолжают жить.
            logger.warning(
                "telegram: доставка события провалена request_id={}: {}",
                request_id,
                exc,
            )

    async def _route_stream_event(
        self,
        *,
        request_id: str,
        chat_id: int,
        reply_to_message_id: int | None,
        outbound: AnswerDelta | ProgressStep | AnswerFinal | AnswerReset,
    ) -> None:
        """Выбирает обработчик события по его типу."""
        if isinstance(outbound, AnswerReset):
            await self._handle_answer_reset(request_id=request_id, chat_id=chat_id)
        elif isinstance(outbound, AnswerDelta):
            accumulated = self._stream_buffers.get(request_id, "") + outbound.delta
            self._stream_buffers[request_id] = accumulated
            await self._handle_intermediate_message(
                request_id=request_id,
                chat_id=chat_id,
                content=accumulated,
            )
        elif isinstance(outbound, ProgressStep):
            await self._handle_progress_step(
                request_id=request_id,
                chat_id=chat_id,
                reply_to_message_id=reply_to_message_id,
                text=outbound.text,
            )
        elif isinstance(outbound, AnswerFinal):
            self._stream_buffers.pop(request_id, None)
            await self._handle_final_message(
                request_id=request_id,
                chat_id=chat_id,
                reply_to_message_id=reply_to_message_id,
                content=outbound.text,
            )
            if outbound.attachments:
                await self._artifact_delivery.send_attachments(
                    chat_id=chat_id,
                    request_id=request_id,
                    attachments=outbound.attachments,
                )

    # ── Черновик стрима ──────────────────────────────────────────────

    async def _handle_answer_reset(self, *, request_id: str, chat_id: int) -> None:
        """Сброс draft-стрима: предыдущий сегмент был preface → черновик назад в «думаю».

        Накопитель дельт обнуляем (preface не должен попасть в финал), отложенный
        снапшот коалесера — тоже. Лог preface приходит отдельным ProgressStep,
        anchor не трогаем.
        """
        if request_id in self._finalized:
            return
        self._stream_buffers[request_id] = ""
        coalescer = self._draft_coalescers.get(request_id)
        if coalescer is not None:
            coalescer.reset()
        await self.send_thinking_draft(chat_id=chat_id, request_id=request_id)

    async def _handle_intermediate_message(
        self,
        *,
        request_id: str,
        chat_id: int,
        content: str,
    ) -> None:
        """Отдаёт накопленный ответ в черновик через коалесер запроса."""
        if request_id in self._finalized:
            return
        coalescer = self._draft_coalescers.get(request_id)
        if coalescer is None:
            coalescer = DraftStreamCoalescer(
                send=partial(
                    self._push_stream_draft, chat_id=chat_id, request_id=request_id
                )
            )
            self._draft_coalescers[request_id] = coalescer
        await coalescer.submit(content)

    async def _push_stream_draft(
        self, content: str, *, chat_id: int, request_id: str
    ) -> None:
        """Кладёт снапшот стрима в черновик, обрезая его под лимиты Telegram."""
        await self._push_draft(
            chat_id=chat_id,
            request_id=request_id,
            markdown=content[-RICH_MESSAGE_CHAR_LIMIT:],
            plain_text=tail_by_utf16_limit(content),
        )

    async def _push_draft(
        self, *, chat_id: int, request_id: str, markdown: str, plain_text: str
    ) -> None:
        """Обновляет черновик rich-разметкой; после отказа чата — plain-текстом.

        Отказ rich-черновика липкий на весь запрос: чат, который не принял
        разметку один раз, не примет её и дальше — незачем тратить на это вызов.
        """
        draft_id = self.draft_id(request_id)
        if request_id not in self._plain_draft_requests:
            try:
                await self._bot.send_rich_message_draft(
                    chat_id=chat_id,
                    draft_id=draft_id,
                    rich_message=InputRichMessage(
                        markdown=markdown, skip_entity_detection=True
                    ),
                )
                return
            except TelegramBadRequest as exc:
                logger.warning(
                    "telegram: rich-черновик отвергнут request_id={}, "
                    "дальше plain: {}",
                    request_id,
                    exc,
                )
                self._plain_draft_requests.add(request_id)

        await self._bot.send_message_draft(
            chat_id=chat_id, draft_id=draft_id, text=plain_text
        )

    # ── Anchor прогресса ─────────────────────────────────────────────

    async def _handle_progress_step(
        self,
        *,
        request_id: str,
        chat_id: int,
        reply_to_message_id: int | None,
        text: str,
    ) -> None:
        """Добавляет шаг прогресса в anchor-сообщение (создаёт его при первом шаге)."""
        if request_id in self._finalized:
            return
        self._progress_steps.setdefault(request_id, []).append(text)
        progress_text = format_progress(self._progress_steps[request_id])

        anchor_id = self._anchor_messages.get(request_id)
        if anchor_id is not None:
            anchor_alive = await self._edit_anchor(
                chat_id=chat_id,
                request_id=request_id,
                anchor_id=anchor_id,
                progress_text=progress_text,
            )
            if anchor_alive:
                return
            self._anchor_messages.pop(request_id, None)

        await self._create_anchor(
            chat_id=chat_id,
            request_id=request_id,
            reply_to_message_id=reply_to_message_id,
            progress_text=progress_text,
        )

    async def _edit_anchor(
        self, *, chat_id: int, request_id: str, anchor_id: int, progress_text: str
    ) -> bool:
        """Правит anchor прогресса; False — сообщения нет, anchor нужно создать заново."""
        try:
            await self._bot.edit_message_text(
                chat_id=chat_id, message_id=anchor_id, text=progress_text
            )
        except TelegramBadRequest as exc:
            lowered = str(exc).lower()
            if "message to edit not found" in lowered:
                return False
            if "message is not modified" not in lowered:
                logger.warning(
                    "telegram: правка anchor провалена request_id={}: {}",
                    request_id,
                    exc,
                )
        except TelegramAPIError as exc:
            logger.warning(
                "telegram: правка anchor провалена request_id={}: {}", request_id, exc
            )
        return True

    async def _create_anchor(
        self,
        *,
        chat_id: int,
        request_id: str,
        reply_to_message_id: int | None,
        progress_text: str,
    ) -> None:
        """Создаёт anchor-сообщение с прогресс-логом и запоминает его id."""
        try:
            sent_message = await self._bot.send_message(
                chat_id=chat_id,
                text=progress_text,
                reply_to_message_id=reply_to_message_id,
            )
        except TelegramAPIError as exc:
            logger.warning(
                "telegram: anchor не создан request_id={}: {}", request_id, exc
            )
            return
        self._anchor_messages[request_id] = sent_message.message_id

    async def _delete_anchor(
        self, *, chat_id: int, request_id: str, anchor_id: int
    ) -> None:
        """Удаляет anchor после финала — лог уже уехал в финальный пузырь."""
        try:
            await self._bot.delete_message(chat_id=chat_id, message_id=anchor_id)
        except TelegramAPIError as exc:
            logger.warning(
                "telegram: anchor не удалён request_id={}: {}", request_id, exc
            )

    # ── Финал ────────────────────────────────────────────────────────

    async def _handle_final_message(
        self,
        *,
        request_id: str,
        chat_id: int,
        reply_to_message_id: int | None,
        content: str,
    ) -> None:
        """Финал A*: rich-пузырь [свёрнутый лог + ответ]; гасит draft, удаляет anchor.

        Финал — всегда новый send (не edit anchor): только приход нового сообщения
        гасит эфемерный черновик. Anchor с прогрессом удаляется — лог не теряется,
        он переезжает в свёрнутый `<details>` финального пузыря.
        """
        anchor_id = self._anchor_messages.pop(request_id, None)
        progress_lines = _progress_log_lines(self._progress_steps.pop(request_id, []))
        self._finalized.add(request_id)

        if content or progress_lines:
            await self._send_final_bubbles(
                chat_id=chat_id,
                request_id=request_id,
                reply_to_message_id=reply_to_message_id,
                progress_lines=progress_lines,
                content=content,
            )

        if anchor_id is not None:
            await self._delete_anchor(
                chat_id=chat_id, request_id=request_id, anchor_id=anchor_id
            )

    async def _send_final_bubbles(
        self,
        *,
        chat_id: int,
        request_id: str,
        reply_to_message_id: int | None,
        progress_lines: list[str],
        content: str,
    ) -> None:
        """Шлёт финал rich-частями: лог в первой части либо отдельным сообщением."""
        log_overhead = _details_overhead(progress_lines)
        if log_overhead < _OVERSIZED_LOG_RESERVE:
            parts_raw = split_rich_markdown(content, first_part_reserve=log_overhead)
            parts_rich = [
                compose_final_markdown(
                    progress_lines=progress_lines, content=parts_raw[0]
                ),
                *parts_raw[1:],
            ]
            await self._send_rich_parts(
                chat_id=chat_id,
                request_id=request_id,
                reply_to_message_id=reply_to_message_id,
                parts_rich=parts_rich,
                parts_raw=parts_raw,
                fallback_log_lines=progress_lines,
            )
            return

        # Лог-гигант: он уходит первым сообщением (и забирает reply), финал —
        # без `<details>`, иначе обвязка не оставит места самому ответу.
        await self._send_plain_chunks(
            chat_id=chat_id,
            request_id=request_id,
            text="\n".join(progress_lines),
            reply_to_message_id=reply_to_message_id,
        )
        if not content:
            return
        parts_raw = split_rich_markdown(content)
        await self._send_rich_parts(
            chat_id=chat_id,
            request_id=request_id,
            reply_to_message_id=None,
            parts_rich=parts_raw,
            parts_raw=parts_raw,
            fallback_log_lines=[],
        )

    async def _send_rich_parts(
        self,
        *,
        chat_id: int,
        request_id: str,
        reply_to_message_id: int | None,
        parts_rich: list[str],
        parts_raw: list[str],
        fallback_log_lines: list[str],
    ) -> None:
        """Шлёт rich-части подряд; отказ разметки переводит остаток на plain-чанки."""
        for index, part in enumerate(parts_rich):
            reply_target = reply_to_message_id if index == 0 else None
            try:
                await self._bot.send_rich_message(
                    chat_id=chat_id,
                    rich_message=InputRichMessage(
                        markdown=part, skip_entity_detection=True
                    ),
                    reply_parameters=(
                        ReplyParameters(message_id=reply_target)
                        if reply_target is not None
                        else None
                    ),
                )
            except TelegramBadRequest as exc:
                logger.warning(
                    "telegram: rich-часть {} отвергнута request_id={}, "
                    "остаток уходит plain: {}",
                    index,
                    request_id,
                    exc,
                )
                await self._send_plain_remainder(
                    chat_id=chat_id,
                    request_id=request_id,
                    reply_to_message_id=reply_target,
                    parts_raw=parts_raw,
                    first_failed_index=index,
                    progress_lines=fallback_log_lines,
                )
                return
            except TelegramAPIError as exc:
                logger.error(
                    "telegram: финал оборван на части {} request_id={}: {}",
                    index,
                    request_id,
                    exc,
                )
                return

    async def _send_plain_remainder(
        self,
        *,
        chat_id: int,
        request_id: str,
        reply_to_message_id: int | None,
        parts_raw: list[str],
        first_failed_index: int,
        progress_lines: list[str],
    ) -> None:
        """Досылает сырой остаток ответа plain-ом — без дубля уже отправленных частей."""
        remainder = _BLOCK_SEPARATOR.join(parts_raw[first_failed_index:])
        if first_failed_index == 0:
            # Лог ехал внутри первой части — вместе с ней он и не дошёл.
            remainder = compose_plain_fallback(
                progress_lines=progress_lines, content=remainder
            )
        await self._send_plain_chunks(
            chat_id=chat_id,
            request_id=request_id,
            text=remainder,
            reply_to_message_id=reply_to_message_id,
        )

    async def _send_plain_chunks(
        self,
        *,
        chat_id: int,
        request_id: str,
        text: str,
        reply_to_message_id: int | None,
    ) -> None:
        """Шлёт текст plain-чанками без разметки; первый сбой обрывает цепочку."""
        for index, chunk in enumerate(chunk_by_utf16_limit(text)):
            try:
                await self._bot.send_message(
                    chat_id=chat_id,
                    text=chunk,
                    reply_to_message_id=reply_to_message_id if index == 0 else None,
                    parse_mode=None,
                )
            except TelegramAPIError as exc:
                logger.error(
                    "telegram: plain-финал оборван на чанке {} request_id={}: {}",
                    index,
                    request_id,
                    exc,
                )
                return


def _progress_log_lines(steps: list[str]) -> list[str]:
    """Возвращает строки прогресс-лога в том же виде, в каком их показывает anchor."""
    return format_progress(steps).splitlines()


def _details_overhead(progress_lines: list[str]) -> int:
    """Считает, сколько символов лимита съедает обвязка лога вокруг контента.

    Меряется на пробе, а не на пустом контенте: в счёт должен попасть и отступ
    между `<details>` и ответом, иначе первая часть вылезет за лимит.
    """
    probe = "x"
    wrapped = compose_final_markdown(progress_lines=progress_lines, content=probe)
    return len(wrapped) - len(probe)
