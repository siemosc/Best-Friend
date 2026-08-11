"""Тесты троттлинга черновика: DraftStreamCoalescer.

Внешний критерий — контракт коалесера: не больше одной отправки в окно, после
окна уезжает свежий снапшот, сбой Telegram не выходит наружу, фоновых задач
у коалесера нет. Часы подменяются, иначе тест зависит от скорости машины.
"""

import inspect
from unittest.mock import AsyncMock, MagicMock

from aiogram.exceptions import TelegramAPIError
import pytest

from bestfiend.telegram import draft_stream
from bestfiend.telegram.draft_stream import DraftStreamCoalescer


class _FrozenClock:
    """Управляемые монотонные часы: время двигает только тест."""

    def __init__(self) -> None:
        self._now = 1_000.0

    def __call__(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> _FrozenClock:
    frozen = _FrozenClock()
    monkeypatch.setattr(draft_stream, "monotonic", frozen)
    return frozen


def _telegram_error(message: str = "Too Many Requests") -> TelegramAPIError:
    return TelegramAPIError(method=MagicMock(), message=message)


@pytest.mark.asyncio
async def test_submits_inside_window_collapse_to_first(clock: _FrozenClock) -> None:
    """Пачка снапшотов в одном окне → одна отправка, самая первая."""
    send = AsyncMock()
    coalescer = DraftStreamCoalescer(send=send)

    await coalescer.submit("При")
    clock.advance(0.2)
    await coalescer.submit("Приве")
    clock.advance(0.2)
    await coalescer.submit("Привет")

    send.assert_awaited_once_with("При")


@pytest.mark.asyncio
async def test_submit_after_window_sends_fresh_snapshot(clock: _FrozenClock) -> None:
    """Снапшот за границей окна уезжает целиком, а не по частям."""
    send = AsyncMock()
    coalescer = DraftStreamCoalescer(send=send)

    await coalescer.submit("При")
    clock.advance(0.5)
    await coalescer.submit("Приве")
    clock.advance(0.6)
    await coalescer.submit("Привет")

    assert [call.args[0] for call in send.await_args_list] == ["При", "Привет"]


@pytest.mark.asyncio
async def test_send_failure_does_not_propagate(clock: _FrozenClock) -> None:
    """Сбой отправки черновика гасится внутри — потребитель стрима живёт дальше."""
    send = AsyncMock(side_effect=_telegram_error())
    coalescer = DraftStreamCoalescer(send=send)

    await coalescer.submit("Привет")

    send.assert_awaited_once()


@pytest.mark.asyncio
async def test_reset_reopens_window(clock: _FrozenClock) -> None:
    """После reset следующий снапшот уходит сразу, не дожидаясь конца окна."""
    send = AsyncMock()
    coalescer = DraftStreamCoalescer(send=send)

    await coalescer.submit("preface")
    clock.advance(0.1)
    coalescer.reset()
    await coalescer.submit("ответ")

    assert [call.args[0] for call in send.await_args_list] == ["preface", "ответ"]


@pytest.mark.asyncio
async def test_custom_interval_is_respected(clock: _FrozenClock) -> None:
    """Окно троттлинга берётся из min_interval_s, а не из константы по умолчанию."""
    send = AsyncMock()
    coalescer = DraftStreamCoalescer(send=send, min_interval_s=5.0)

    await coalescer.submit("первый")
    clock.advance(1.5)
    await coalescer.submit("второй")

    send.assert_awaited_once_with("первый")


def test_coalescer_owns_no_background_work() -> None:
    """Троттлинг сделан на времени: ни задач, ни таймеров — нечему протекать."""
    source = inspect.getsource(DraftStreamCoalescer)

    assert "create_task" not in source
    assert "ensure_future" not in source
    assert "call_later" not in source
    assert "sleep" not in source
