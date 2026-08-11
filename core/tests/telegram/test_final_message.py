"""Тесты доставки вывода A*: OutboundDelivery на нативных Rich Messages.

Внешний критерий — контракт этапа: финал уходит rich-частями (лог свёрнут в
`<details>`, reply только у первой), отказ разметки досылает ровно недошедший
остаток plain-ом, черновик стрима после отказа навсегда деградирует до plain,
а сбой Telegram не выходит из dispatch наружу.
"""

from unittest.mock import AsyncMock, MagicMock

from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
import pytest

from bestfiend.contracts.events import AnswerDelta, ProgressStep
from bestfiend.telegram import draft_stream
from bestfiend.telegram.formatters import (
    RICH_MESSAGE_CHAR_LIMIT,
    TELEGRAM_TEXT_LIMIT,
    THINKING_DRAFT_MARKDOWN,
    compose_final_markdown,
    format_progress,
    split_rich_markdown,
    utf16_len,
)
from bestfiend.telegram.outbound_delivery import OutboundDelivery


_PARAGRAPH_LENGTH = 1_000
_PARAGRAPHS_OVER_LIMIT = 50


def _delivery() -> tuple[OutboundDelivery, MagicMock]:
    """OutboundDelivery с пустым per-request стейтом и замоканным aiogram-ботом."""
    tg = MagicMock()
    tg.send_message = AsyncMock(return_value=MagicMock(message_id=999))
    tg.edit_message_text = AsyncMock()
    tg.delete_message = AsyncMock()
    tg.send_message_draft = AsyncMock()
    tg.send_rich_message = AsyncMock(return_value=MagicMock(message_id=1000))
    tg.send_rich_message_draft = AsyncMock()
    return OutboundDelivery(bot=tg, artifact_delivery=MagicMock()), tg


def _bad_request(message: str = "Bad Request: RICH_MESSAGE_INVALID") -> Exception:
    return TelegramBadRequest(method=MagicMock(), message=message)


def _rich_markdowns(tg: MagicMock) -> list[str]:
    return [
        call.kwargs["rich_message"].markdown
        for call in tg.send_rich_message.call_args_list
    ]


def _plain_texts(tg: MagicMock) -> list[str]:
    return [call.kwargs["text"] for call in tg.send_message.call_args_list]


def _long_content() -> str:
    """Контент заведомо длиннее лимита rich-сообщения, режется по параграфам."""
    return "\n\n".join(
        f"{index:03d} {'П' * _PARAGRAPH_LENGTH}"
        for index in range(_PARAGRAPHS_OVER_LIMIT)
    )


def _expected_raw_parts(steps: list[str], content: str) -> list[str]:
    """Сырые части ответа так, как их посчитает доставка: с резервом под обвязку."""
    progress_lines = format_progress(steps).splitlines()
    probe = "x"
    overhead = (
        len(compose_final_markdown(progress_lines=progress_lines, content=probe)) - 1
    )
    return split_rich_markdown(content, first_part_reserve=overhead)


@pytest.mark.asyncio
async def test_final_rich_bubble_carries_log_and_reply() -> None:
    """Прогресс + ответ → одна rich-часть с `<details>`, reply у неё, anchor удалён."""
    delivery, tg = _delivery()
    delivery._anchor_messages["r1"] = 555
    delivery._progress_steps["r1"] = ["Ищу", "Читаю"]

    await delivery._handle_final_message(
        request_id="r1", chat_id=1, reply_to_message_id=42, content="Готовый ответ"
    )

    tg.send_rich_message.assert_awaited_once()
    kwargs = tg.send_rich_message.call_args.kwargs
    assert kwargs["rich_message"].markdown.startswith("<details>")
    assert "Ищу" in kwargs["rich_message"].markdown
    assert kwargs["rich_message"].markdown.endswith("Готовый ответ")
    assert kwargs["rich_message"].skip_entity_detection is True
    assert kwargs["reply_parameters"].message_id == 42
    tg.delete_message.assert_awaited_once_with(chat_id=1, message_id=555)
    assert "r1" in delivery._finalized
    assert "r1" not in delivery._progress_steps


@pytest.mark.asyncio
async def test_final_without_log_sends_bare_content() -> None:
    """Без прогресса → markdown равен ответу, reply и delete не нужны."""
    delivery, tg = _delivery()

    await delivery._handle_final_message(
        request_id="r2", chat_id=1, reply_to_message_id=None, content="Просто ответ"
    )

    assert _rich_markdowns(tg) == ["Просто ответ"]
    assert tg.send_rich_message.call_args.kwargs["reply_parameters"] is None
    tg.delete_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_final_empty_content_with_log_sends_details_only() -> None:
    """Пустой ответ + лог → пузырь из одного свёрнутого лога, anchor удалён."""
    delivery, tg = _delivery()
    delivery._anchor_messages["r3"] = 7
    delivery._progress_steps["r3"] = ["Шаг"]

    await delivery._handle_final_message(
        request_id="r3", chat_id=1, reply_to_message_id=None, content=""
    )

    markdowns = _rich_markdowns(tg)
    assert len(markdowns) == 1
    assert markdowns[0].startswith("<details>")
    assert markdowns[0].endswith("</details>")
    assert "Шаг" in markdowns[0]
    tg.delete_message.assert_awaited_once_with(chat_id=1, message_id=7)


@pytest.mark.asyncio
async def test_final_empty_content_without_log_sends_nothing() -> None:
    """Пустой ответ без лога → ни одного сообщения, но anchor всё равно снят."""
    delivery, tg = _delivery()
    delivery._anchor_messages["r4"] = 11

    await delivery._handle_final_message(
        request_id="r4", chat_id=1, reply_to_message_id=None, content=""
    )

    tg.send_rich_message.assert_not_awaited()
    tg.send_message.assert_not_awaited()
    tg.delete_message.assert_awaited_once_with(chat_id=1, message_id=11)


@pytest.mark.asyncio
async def test_final_long_content_parts_fit_rich_limit() -> None:
    """Длинный ответ + лог → каждая часть влезает в лимит, обвязка только в первой."""
    delivery, tg = _delivery()
    delivery._progress_steps["r5"] = ["Ищу", "Читаю"]
    content = _long_content()

    await delivery._handle_final_message(
        request_id="r5", chat_id=1, reply_to_message_id=None, content=content
    )

    markdowns = _rich_markdowns(tg)
    assert len(markdowns) > 1
    assert all(len(part) <= RICH_MESSAGE_CHAR_LIMIT for part in markdowns)
    assert markdowns[0].startswith("<details>")
    assert all("<details>" not in part for part in markdowns[1:])
    tg.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_final_first_part_rejected_falls_back_to_plain() -> None:
    """Отказ разметки на первой части → весь ответ вместе с логом уходит plain-ом."""
    delivery, tg = _delivery()
    delivery._progress_steps["r6"] = ["Ищу"]
    tg.send_rich_message.side_effect = _bad_request()

    await delivery._handle_final_message(
        request_id="r6", chat_id=1, reply_to_message_id=42, content="Готовый ответ"
    )

    plain = "".join(_plain_texts(tg))
    assert "Готовый ответ" in plain
    assert "Ищу" in plain
    first_call = tg.send_message.call_args_list[0].kwargs
    assert first_call["reply_to_message_id"] == 42
    assert first_call["parse_mode"] is None


@pytest.mark.asyncio
async def test_final_second_part_rejected_sends_only_remainder() -> None:
    """Отказ на второй части → plain-ом уходит только остаток, без дубля и без лога."""
    delivery, tg = _delivery()
    steps = ["Ищу"]
    delivery._progress_steps["r7"] = list(steps)
    content = _long_content()
    parts_raw = _expected_raw_parts(steps, content)
    assert len(parts_raw) == 2
    tg.send_rich_message.side_effect = [MagicMock(), _bad_request()]

    await delivery._handle_final_message(
        request_id="r7", chat_id=1, reply_to_message_id=None, content=content
    )

    assert tg.send_rich_message.await_count == 2
    plain = "".join(_plain_texts(tg))
    assert plain == parts_raw[1]
    assert "Ищу" not in plain
    assert parts_raw[0][:200] not in plain


@pytest.mark.asyncio
async def test_final_giant_log_goes_separate_plain_message() -> None:
    """Лог-гигант не влезает в обвязку → уезжает plain-ом, финал идёт без details."""
    delivery, tg = _delivery()
    delivery._progress_steps["r8"] = ["Ш" * _PARAGRAPH_LENGTH] * 20

    await delivery._handle_final_message(
        request_id="r8", chat_id=1, reply_to_message_id=42, content="Короткий ответ"
    )

    plain_chunks = _plain_texts(tg)
    assert len(plain_chunks) > 1
    assert all(utf16_len(chunk) <= TELEGRAM_TEXT_LIMIT for chunk in plain_chunks)
    assert tg.send_message.call_args_list[0].kwargs["reply_to_message_id"] == 42
    assert _rich_markdowns(tg) == ["Короткий ответ"]
    assert tg.send_rich_message.call_args.kwargs["reply_parameters"] is None


@pytest.mark.asyncio
async def test_progress_step_after_final_is_noop() -> None:
    """ProgressStep после финала — no-op, anchor не создаётся."""
    delivery, tg = _delivery()
    delivery._finalized.add("r9")

    await delivery._handle_progress_step(
        request_id="r9", chat_id=1, reply_to_message_id=None, text="late"
    )

    tg.send_message.assert_not_awaited()
    tg.edit_message_text.assert_not_awaited()
    assert "r9" not in delivery._progress_steps


@pytest.mark.asyncio
async def test_answer_reset_after_final_is_noop() -> None:
    """AnswerReset после финала — черновик не трогаем."""
    delivery, tg = _delivery()
    delivery._finalized.add("r10")

    await delivery._handle_answer_reset(request_id="r10", chat_id=1)

    tg.send_rich_message_draft.assert_not_awaited()
    tg.send_message_draft.assert_not_awaited()


@pytest.mark.asyncio
async def test_answer_reset_clears_buffer_and_redraws_thinking() -> None:
    """AnswerReset обнуляет буфер стрима и возвращает черновик в «думаю»."""
    delivery, tg = _delivery()
    delivery._stream_buffers["r11"] = "сейчас поищу"

    await delivery._handle_answer_reset(request_id="r11", chat_id=1)

    assert delivery._stream_buffers["r11"] == ""
    tg.send_rich_message_draft.assert_awaited_once()
    kwargs = tg.send_rich_message_draft.call_args.kwargs
    assert kwargs["draft_id"] == OutboundDelivery.draft_id("r11")
    assert kwargs["rich_message"].markdown == THINKING_DRAFT_MARKDOWN


@pytest.mark.asyncio
async def test_thinking_draft_degrades_to_plain_then_noop() -> None:
    """Чат не принял rich-черновик → пустой plain-черновик; отказал и он → тишина."""
    delivery, tg = _delivery()
    tg.send_rich_message_draft.side_effect = _bad_request()
    tg.send_message_draft.side_effect = TelegramAPIError(
        method=MagicMock(), message="drafts are not available"
    )

    await delivery.send_thinking_draft(chat_id=1, request_id="r12")

    tg.send_message_draft.assert_awaited_once()
    assert tg.send_message_draft.call_args.kwargs["text"] == ""


@pytest.mark.asyncio
async def test_stream_draft_sends_full_buffer_as_rich(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Дельта уходит в черновик rich-разметкой целиком накопленного ответа."""
    delivery, tg = _delivery()
    _advance_clock_each_call(monkeypatch)

    for delta in ("При", "вет"):
        await delivery.dispatch_stream_event(
            request_id="r13",
            chat_id=1,
            reply_to_message_id=None,
            outbound=AnswerDelta(request_id="r13", delta=delta),
        )

    markdowns = [
        call.kwargs["rich_message"].markdown
        for call in tg.send_rich_message_draft.call_args_list
    ]
    assert markdowns == ["При", "Привет"]


@pytest.mark.asyncio
async def test_stream_draft_degradation_to_plain_is_sticky(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Отказ rich-черновика переводит запрос на plain навсегда, буфер не теряется."""
    delivery, tg = _delivery()
    _advance_clock_each_call(monkeypatch)
    tg.send_rich_message_draft.side_effect = _bad_request()

    for delta in ("При", "вет"):
        await delivery.dispatch_stream_event(
            request_id="r14",
            chat_id=1,
            reply_to_message_id=None,
            outbound=AnswerDelta(request_id="r14", delta=delta),
        )

    assert tg.send_rich_message_draft.await_count == 1
    assert [call.kwargs["text"] for call in tg.send_message_draft.call_args_list] == [
        "При",
        "Привет",
    ]


@pytest.mark.asyncio
async def test_dispatch_swallows_telegram_failure() -> None:
    """Кидающий бот на прогресс-шаге не выносит исключение из dispatch наружу."""
    delivery, tg = _delivery()
    tg.send_message.side_effect = TelegramAPIError(
        method=MagicMock(), message="chat not found"
    )

    await delivery.dispatch_stream_event(
        request_id="r15",
        chat_id=1,
        reply_to_message_id=None,
        outbound=ProgressStep(request_id="r15", text="Ищу"),
    )

    tg.send_message.assert_awaited_once()
    assert "r15" not in delivery._anchor_messages


def _advance_clock_each_call(monkeypatch: pytest.MonkeyPatch) -> None:
    """Часы коалесера прыгают за окно троттлинга на каждом обращении."""
    ticks = iter(range(0, 10_000, 10))
    monkeypatch.setattr(draft_stream, "monotonic", lambda: float(next(ticks)))
