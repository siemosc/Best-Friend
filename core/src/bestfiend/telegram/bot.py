"""Telegram bot — встроенный edge core monolith.

Проводка: принимает апдейт, авторизует (`UserService`, welcome inline при
первой регистрации), публикует InputEvent через publish-контракт
(`CoreRuntime.publish_input_event`) и читает подписку стрима. Разорванный
Telegram-ом бурст (альбом, forward + комментарий) склеивается в один пакет
inbox-агрегатором до входа в хендлер — контентный путь один на пакет. Голосовые
и аудио пакета расшифровываются портом `SpeechTranscriber` и уходят в событие
текстом-маркером, артефакта из них не создаётся. Сама доставка и приём файлов —
в коллабораторах: `OutboundDelivery` (draft/anchor/финал),
`AttachmentIngestionService` (скачивание вложений), `ArtifactDelivery` (отдача
артефактов юзеру). `/web` выдаёт binding-code через `AuthService`.
"""

import asyncio
from collections.abc import Callable, Coroutine, Iterable
from typing import Any

from aiogram import Bot, Dispatcher, F
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandStart
from aiogram.types import Message
from loguru import logger
from uuid6 import uuid7

from bestfiend.ai.stt.contracts import SpeechTranscriber
from bestfiend.artifacts.service import ArtifactService
from bestfiend.contracts.artifacts import ArtifactRef
from bestfiend.contracts.events import InputEvent, OutboundEventSource
from bestfiend.control_plane.auth.errors import AuthError, UserStatusError
from bestfiend.control_plane.auth.service import AuthService
from bestfiend.control_plane.users.errors import (
    UserConflictError,
    UserUnavailableError,
)
from bestfiend.control_plane.users.models import UserProfile
from bestfiend.control_plane.users.service import UserService
from bestfiend.telegram.artifact_delivery import ArtifactDelivery
from bestfiend.telegram.attachment_ingest import (
    AttachmentInfo,
    AttachmentIngestionService,
    AudioAttachmentInfo,
    extract_attachment_info,
    extract_audio_info,
)
from bestfiend.telegram.errors import AuthorizationFailedError
from bestfiend.telegram.inbox import (
    INBOX_BUNDLE_KEY,
    InboxAggregator,
    InboxMiddleware,
)
from bestfiend.telegram.outbound_delivery import OutboundDelivery
from bestfiend.telegram.request_correlation import (
    RequestCorrelation,
    ensure_request_correlation,
)


_WELCOME_MESSAGE = "Привет! Твой аккаунт создан. Я готов помогать."
_DISCORD_UNAVAILABLE_TEXT = "Discord-канал ещё не активен. Команда временно недоступна."
_WEB_UNAVAILABLE_TEXT = "Веб-интерфейс временно недоступен. Попробуй позже."
_WEB_PENDING_TEXT = "Аккаунт ещё не активирован администратором — после активации команда станет доступна."
_STT_DISABLED_TEXT = "Голосовые сейчас не обрабатываются — напиши текстом."
_STT_UNRECOGNIZED_TEXT = "Не разобрал голосовое — попробуй ещё раз или напиши текстом."
_SECONDS_IN_MINUTE = 60
_DEFAULT_STT_MAX_DURATION_S = 300

# Traced ingress: `(*, event, request_correlation) -> None`. Бот не знает про
# GraphRuntime — зовёт publish-контракт (CoreRuntime.publish_input_event),
# который оборачивает обработку в root-trace.
PublishInputEvent = Callable[..., Coroutine[Any, Any, None]]


class TelegramBot:
    """Telegram edge: ingress, авторизация, проводка стрима (in-process только)."""

    def __init__(
        self,
        *,
        bot_token: str,
        user_service: UserService,
        publish_input_event: PublishInputEvent,
        artifacts: ArtifactService,
        outbound_source: OutboundEventSource,
        attachment_max_size_bytes: int,
        allowed_user_ids: Iterable[int] | None = None,
        auth_service: AuthService | None = None,
        binding_code_ttl_s: int = 600,
        inbox_debounce_s: float = 0.5,
        transcriber: SpeechTranscriber | None = None,
        stt_max_duration_s: int = _DEFAULT_STT_MAX_DURATION_S,
    ) -> None:
        self.bot = Bot(token=bot_token)
        self.dispatcher = Dispatcher()
        self.allowed_user_ids: set[int] | None = (
            set(allowed_user_ids) if allowed_user_ids else None
        )
        self._user_service = user_service
        self._publish_input_event_fn = publish_input_event
        self._outbound_source = outbound_source
        self._auth_service = auth_service
        self._binding_code_ttl_s = binding_code_ttl_s
        self._transcriber = transcriber
        self._stt_max_duration_s = stt_max_duration_s
        self._outbound = OutboundDelivery(
            bot=self.bot,
            artifact_delivery=ArtifactDelivery(bot=self.bot, artifacts=artifacts),
        )
        self._ingest = AttachmentIngestionService(
            bot=self.bot,
            artifacts=artifacts,
            max_size_bytes=attachment_max_size_bytes,
        )
        self.dispatcher.message.outer_middleware(
            InboxMiddleware(
                aggregator=InboxAggregator(window_s=inbox_debounce_s),
                is_allowed=self._is_allowed,
            )
        )

        self._setup_handlers()

    async def start_polling(self) -> None:
        """Запускает polling режим получения обновлений."""
        logger.info("telegram: starting Telegram polling")
        await self.dispatcher.start_polling(self.bot, handle_signals=False)

    async def close(self) -> None:
        """Закрывает HTTP session Telegram бота."""
        await self.bot.session.close()

    def _is_allowed(self, user_id: int | None) -> bool:
        if user_id is None:
            return False
        if self.allowed_user_ids is None:
            return True
        return user_id in self.allowed_user_ids

    def _setup_handlers(self) -> None:
        async def on_start(message: Message) -> None:
            if not self._is_allowed(
                message.from_user.id if message.from_user else None
            ):
                logger.info(
                    "telegram: access denied user_id={}",
                    message.from_user.id if message.from_user else "<unknown>",
                )
                return
            await message.answer("Я на связи. Отправь любое сообщение.")

        async def on_discord(message: Message) -> None:
            # Stub: live Discord channel не реализован. Команда отвечает
            # юзеру что канал недоступен.
            telegram_user_id = message.from_user.id if message.from_user else None
            if not self._is_allowed(telegram_user_id):
                return
            await message.answer(_DISCORD_UNAVAILABLE_TEXT)

        async def on_web(message: Message) -> None:
            await self._handle_web(message)

        async def on_content(message: Message, **data: Any) -> None:
            # Путь через middleware: ACL уже проверен, анкер пакета в message,
            # сам пакет — в контексте aiogram под INBOX_BUNDLE_KEY.
            bundle: list[Message] = data.get(INBOX_BUNDLE_KEY) or []
            if not bundle:
                # Контекста нет — хендлер позвали мимо middleware, значит ACL
                # никто не проверял.
                telegram_user_id = message.from_user.id if message.from_user else None
                if not self._is_allowed(telegram_user_id):
                    logger.info(
                        "telegram: access denied user_id={}",
                        telegram_user_id
                        if telegram_user_id is not None
                        else "<unknown>",
                    )
                    return
                bundle = [message]
            await self._publish_bundle(bundle)

        self.dispatcher.message.register(on_start, CommandStart())
        self.dispatcher.message.register(on_discord, Command("discord"))
        self.dispatcher.message.register(on_web, Command("web"))
        self.dispatcher.message.register(
            on_content,
            F.text | F.photo | F.document | F.voice | F.audio | F.video | F.video_note,
        )

    async def _handle_web(self, message: Message) -> None:
        """`/web` — выдаёт юзеру 6-значный код для bind в UI.

        `auth_service` опционален: если не передан — graceful skip с понятным
        текстом.
        """
        telegram_user_id = message.from_user.id if message.from_user else None
        if not self._is_allowed(telegram_user_id) or telegram_user_id is None:
            return

        if self._auth_service is None:
            await message.answer(_WEB_UNAVAILABLE_TEXT)
            return

        try:
            profile, _ = await self._user_service.resolve_or_create_by_telegram(
                telegram_user_id,
            )
        except (UserUnavailableError, UserConflictError) as exc:
            logger.warning(
                "telegram: /web resolve failed user_id={}: {}", telegram_user_id, exc
            )
            await message.answer(_WEB_UNAVAILABLE_TEXT)
            return

        if profile.status != "active":
            await message.answer(_WEB_PENDING_TEXT)
            return

        try:
            record = await self._auth_service.generate_binding_code(profile.user_id)
        except UserStatusError:
            await message.answer(_WEB_PENDING_TEXT)
            return
        except AuthError as exc:
            logger.warning(
                "telegram: /web binding-code failed user_id={}: {}",
                profile.user_id,
                exc,
            )
            await message.answer(_WEB_UNAVAILABLE_TEXT)
            return

        ttl_min = self._binding_code_ttl_s // _SECONDS_IN_MINUTE
        await message.answer(
            f"Код для входа в UI: {record.code}\nДействителен {ttl_min} мин."
        )

    async def _publish_bundle(self, bundle: list[Message]) -> None:
        """Пакет бурста → один InputEvent: текст склеен, вложения загружены.

        Анкер (первое сообщение пакета, минимальный message_id) задаёт адрес
        всего бурста: чат, message_id, reply и объект для ответов авторизации.
        Порядок жёсткий: authorize → ранний черновик «думаю» (только если в
        пакете есть аудио — распознавание речи занимает секунды) → расшифровка
        аудио → сборка текста → upload вложений → publish. Authorize идёт до
        upload — artifacts пишутся уже под известный user_id.
        """
        anchor = bundle[0]
        telegram_user_id = anchor.from_user.id if anchor.from_user else None
        if telegram_user_id is None:
            logger.error("telegram: missing from_user.id")
            return

        try:
            profile, request_correlation = await self._authorize_and_correlate(
                anchor, telegram_user_id
            )
        except AuthorizationFailedError:
            return

        if any(extract_audio_info(message) is not None for message in bundle):
            # Юзер видит «думаю» на время скачивания и распознавания; повторный
            # черновик в _publish_input_event идёт под тем же draft_id.
            await self._outbound.send_thinking_draft(
                chat_id=anchor.chat.id,
                request_id=request_correlation.request_id,
            )

        text = await self._compose_bundle_text(
            bundle=bundle,
            error_chat_id=anchor.chat.id,
        )
        infos: list[AttachmentInfo] = [
            info
            for info in (extract_attachment_info(message) for message in bundle)
            if info is not None
        ]
        artifacts: list[ArtifactRef] = []
        if infos:
            artifacts = await self._ingest.download_and_upload_many(
                authorized_user_id=profile.user_id,
                infos=infos,
                error_chat_id=anchor.chat.id,
            )

        if not text and not artifacts:
            logger.info(
                "telegram: bundle chat_id={} message_id={} has no text and no "
                "uploaded artifacts, skipping InputEvent publish",
                anchor.chat.id,
                anchor.message_id,
            )
            return

        reply_to = (
            anchor.reply_to_message.message_id if anchor.reply_to_message else None
        )
        await self._publish_input_event(
            chat_id=anchor.chat.id,
            message_id=anchor.message_id,
            text=text,
            reply_to_message_id=reply_to,
            attached_artifacts=artifacts,
            profile=profile,
            request_correlation=request_correlation,
        )

    async def _compose_bundle_text(
        self,
        *,
        bundle: list[Message],
        error_chat_id: int,
    ) -> str:
        """Текст пакета: подписи, тексты сообщений и расшифровки голосовых по порядку.

        Подпись к голосовому сохраняется всегда — при любом исходе распознавания
        теряется только маркер транскрипции. Каждая ветка отказа пишет юзеру
        сама: это же сообщение гасит ранний черновик «думаю».
        """
        parts: list[str] = []
        stt_disabled_reported = False
        for message in bundle:
            audio_info = extract_audio_info(message)
            if audio_info is None:
                part = message.text or message.caption
                if part:
                    parts.append(part)
                continue

            if message.caption:
                parts.append(message.caption)
            if self._transcriber is None:
                if not stt_disabled_reported:
                    stt_disabled_reported = True
                    await self._notify(error_chat_id, _STT_DISABLED_TEXT)
                continue
            marker = await self._transcribe_audio(
                info=audio_info,
                transcriber=self._transcriber,
                error_chat_id=error_chat_id,
            )
            if marker:
                parts.append(marker)
        return "\n\n".join(parts)

    async def _transcribe_audio(
        self,
        *,
        info: AudioAttachmentInfo,
        transcriber: SpeechTranscriber,
        error_chat_id: int,
    ) -> str | None:
        """Голосовое или аудио → маркер транскрипции для текста пакета.

        None означает, что расшифровки не будет: слишком длинная запись, файл не
        доехал или в записи не разобрана речь. Юзеру про это уже сказано —
        молча отваливается только сбой скачивания, о котором пишет приём вложений.
        """
        if info.duration_s > self._stt_max_duration_s:
            await self._notify(
                error_chat_id,
                f"Запись длиной {info.duration_s} с длиннее лимита "
                f"{self._stt_max_duration_s} с — расшифровать не смогу. "
                "Пришли покороче или напиши текстом.",
            )
            return None

        audio = await self._ingest.download_bytes(
            info=info,
            error_chat_id=error_chat_id,
        )
        if audio is None:
            return None

        transcript = await transcriber.transcribe(audio, info.filename)
        if not transcript:
            await self._notify(error_chat_id, _STT_UNRECOGNIZED_TEXT)
            return None

        if info.kind == "voice":
            return f"[транскрипция голосового: «{transcript}»]"
        return f"[транскрипция аудио «{info.filename}»: «{transcript}»]"

    async def _notify(self, chat_id: int, text: str) -> None:
        """Шлёт юзеру служебное сообщение; сбой Telegram не роняет обработку пакета."""
        try:
            await self.bot.send_message(chat_id=chat_id, text=text)
        except TelegramAPIError as exc:
            logger.warning(
                "telegram: notice delivery failed chat_id={}: {}", chat_id, exc
            )

    async def _authorize_and_correlate(
        self, message: Message, telegram_user_id: int
    ) -> tuple[UserProfile, RequestCorrelation]:
        """In-process authorize + welcome inline + корреляция запроса (request_id).

        Raises AuthorizationFailedError если status != active или сервис недоступен.
        """
        try:
            profile, is_new = await self._user_service.resolve_or_create_by_telegram(
                telegram_user_id,
            )
        except (UserConflictError, UserUnavailableError) as exc:
            logger.warning(
                "telegram: authorize failed telegram_user_id={}: {}",
                telegram_user_id,
                exc,
            )
            await message.answer(
                "Сервис временно недоступен, попробуй ещё раз через пару минут."
            )
            raise AuthorizationFailedError from exc

        if is_new:
            try:
                await self.bot.send_message(
                    chat_id=message.chat.id,
                    text=_WELCOME_MESSAGE,
                )
            except TelegramAPIError as exc:
                logger.warning(
                    "telegram: welcome message failed user_id={}: {}",
                    profile.user_id,
                    exc,
                )

        if profile.status != "active":
            logger.info(
                "telegram: status={} telegram_user_id={} rejected",
                profile.status,
                telegram_user_id,
            )
            if profile.status == "pending":
                await message.answer(
                    "Твой аккаунт ещё не активирован — подожди подтверждения."
                )
            raise AuthorizationFailedError

        request_id = str(uuid7())
        request_correlation = ensure_request_correlation(
            request_id=request_id,
            user_id=str(profile.user_id),
        )
        return profile, request_correlation

    async def _publish_input_event(
        self,
        *,
        chat_id: int,
        message_id: int,
        text: str,
        reply_to_message_id: int | None = None,
        attached_artifacts: list[ArtifactRef] | None = None,
        profile: UserProfile,
        request_correlation: RequestCorrelation,
    ) -> None:
        """In-process subscription → graph_task → dispatch outbound в Telegram.

        Authorize уже сделан вызывающим (`_publish_bundle`): profile и
        request_correlation приходят готовыми — они нужны раньше, до upload
        вложений и STT.

        Инвариант: подписка на стрим открывается ДО старта graph-таска — иначе
        первые события гонки уходят в никуда.
        """
        event_metadata: dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
        }
        if reply_to_message_id is not None:
            event_metadata["reply_to_message_id"] = reply_to_message_id
        event = InputEvent(
            processing_mode="task",
            user_id=profile.user_id,
            message=text,
            channel="telegram",
            metadata=event_metadata,
            request_id=request_correlation.request_id,
            attached_artifacts=attached_artifacts or [],
        )
        logger.info(
            "telegram: open in-process stream request_id={}",
            event.request_id,
        )

        subscription = self._outbound_source.open(event.request_id)
        # Фаза 0 вывода: мгновенный «Thinking…» — сигнал «взялся» до первого
        # события графа.
        await self._outbound.send_thinking_draft(
            chat_id=chat_id, request_id=event.request_id
        )
        graph_task = asyncio.create_task(
            self._publish_input_event_fn(
                event=event, request_correlation=request_correlation
            ),
            name=f"graph-{event.request_id}",
        )
        try:
            async for outbound in subscription:
                await self._outbound.dispatch_stream_event(
                    request_id=event.request_id,
                    chat_id=chat_id,
                    reply_to_message_id=reply_to_message_id,
                    outbound=outbound,
                )
        finally:
            # Дожидаемся graph_task: такт графа завершён, persist поставлен в
            # BackgroundTaskSupervisor (его завершение гарантирует shutdown
            # supervisor'а, не этот await). Exception в task — log, не пробрасываем.
            try:
                await graph_task
            except (asyncio.CancelledError, Exception) as exc:
                logger.debug(
                    "telegram: graph_task wait after subscription closed: {}",
                    exc,
                )
            await subscription.close()
            self._outbound.cleanup_request_state(event.request_id)
