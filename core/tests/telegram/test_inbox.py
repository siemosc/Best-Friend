"""Тесты склейки бурста: InboxAggregator (скользящее окно) и InboxMiddleware."""

import asyncio
from types import SimpleNamespace
from typing import Any, cast

from aiogram.types import Message, TelegramObject
import pytest

from bestfiend.telegram.inbox import (
    INBOX_BUNDLE_KEY,
    BurstKey,
    InboxAggregator,
    InboxMiddleware,
)


# Окно агрегатора в тестах: достаточно короткое, чтобы прогон не тормозил.
_WINDOW_S = 0.05
# Тест скольжения окна шлёт сообщения по шагам внутри окна — запас на джиттер
# таймеров ОС берётся окном побольше.
_SLIDING_WINDOW_S = 0.15
_SLIDING_STEP_S = 0.09

_KEY: BurstKey = (100, 200, None)


def _message(
    message_id: int,
    *,
    chat_id: int = 100,
    from_user_id: int | None = 200,
    thread_id: int | None = None,
    text: str | None = "привет",
    photo: list[str] | None = None,
) -> Message:
    """Фейковое сообщение Telegram: только поля, которые читает inbox."""
    from_user = None if from_user_id is None else SimpleNamespace(id=from_user_id)
    fake = SimpleNamespace(
        message_id=message_id,
        chat=SimpleNamespace(id=chat_id),
        from_user=from_user,
        message_thread_id=thread_id,
        text=text,
        photo=photo,
        document=None,
        voice=None,
        audio=None,
        video=None,
        video_note=None,
    )
    return cast(Message, fake)


class _AggregatorSpy:
    """Фейковый агрегатор: считает вызовы collect и отдаёт заданный пакет."""

    def __init__(self, bundle: list[Message] | None = None) -> None:
        self.calls: list[tuple[BurstKey, Message]] = []
        self._bundle = bundle

    async def collect(self, key: BurstKey, message: Message) -> list[Message] | None:
        self.calls.append((key, message))
        return self._bundle


class _HandlerSpy:
    """Фейковый handler aiogram: запоминает событие и контекст каждого вызова."""

    def __init__(self) -> None:
        self.calls: list[tuple[TelegramObject, dict[str, Any]]] = []

    async def __call__(self, event: TelegramObject, data: dict[str, Any]) -> str:
        self.calls.append((event, data))
        return "handled"


def _middleware(
    aggregator: _AggregatorSpy,
    *,
    is_allowed: bool = True,
) -> InboxMiddleware:
    """Middleware поверх фейкового агрегатора с фиксированным вердиктом доступа."""
    return InboxMiddleware(
        aggregator=cast(InboxAggregator, aggregator),
        is_allowed=lambda _user_id: is_allowed,
    )


@pytest.mark.asyncio
async def test_burst_collected_into_single_bundle() -> None:
    """Два сообщения одного ключа склеиваются в один пакет у владельца окна."""
    aggregator = InboxAggregator(window_s=_WINDOW_S)

    owner = asyncio.create_task(aggregator.collect(_KEY, _message(1)))
    await asyncio.sleep(0)

    assert await aggregator.collect(_KEY, _message(2)) is None
    bundle = await owner

    assert bundle is not None
    assert [msg.message_id for msg in bundle] == [1, 2]


@pytest.mark.asyncio
async def test_each_message_slides_window_deadline() -> None:
    """Сообщение внутри окна продлевает дедлайн: пакет ждёт последнее из бурста."""
    aggregator = InboxAggregator(window_s=_SLIDING_WINDOW_S)

    owner = asyncio.create_task(aggregator.collect(_KEY, _message(1)))
    await asyncio.sleep(_SLIDING_STEP_S)
    assert await aggregator.collect(_KEY, _message(2)) is None
    # Момент прихода третьего уже вне исходного окна первого сообщения —
    # попадёт в тот же пакет, только если дедлайн едет за каждым сообщением.
    await asyncio.sleep(_SLIDING_STEP_S)
    assert await aggregator.collect(_KEY, _message(3)) is None

    bundle = await owner

    assert bundle is not None
    assert [msg.message_id for msg in bundle] == [1, 2, 3]


@pytest.mark.asyncio
async def test_bundle_sorted_by_message_id() -> None:
    """Пакет отсортирован по message_id независимо от порядка прихода."""
    aggregator = InboxAggregator(window_s=_WINDOW_S)

    owner = asyncio.create_task(aggregator.collect(_KEY, _message(30)))
    await asyncio.sleep(0)
    await aggregator.collect(_KEY, _message(10))
    await aggregator.collect(_KEY, _message(20))

    bundle = await owner

    assert bundle is not None
    assert [msg.message_id for msg in bundle] == [10, 20, 30]


@pytest.mark.asyncio
async def test_different_keys_collected_separately() -> None:
    """Другой чат, автор или тред — отдельное окно и отдельный пакет."""
    aggregator = InboxAggregator(window_s=_WINDOW_S)
    keys: list[BurstKey] = [(100, 200, None), (999, 200, None), (100, 777, None), (100, 200, 5)]

    bundles = await asyncio.gather(
        *(
            aggregator.collect(key, _message(index))
            for index, key in enumerate(keys)
        )
    )

    assert [bundle and [msg.message_id for msg in bundle] for bundle in bundles] == [
        [0],
        [1],
        [2],
        [3],
    ]


@pytest.mark.asyncio
async def test_next_burst_starts_with_empty_buffer() -> None:
    """После срабатывания окна следующий бурст собирается с нуля."""
    aggregator = InboxAggregator(window_s=_WINDOW_S)

    first = await aggregator.collect(_KEY, _message(1))
    second = await aggregator.collect(_KEY, _message(2))

    assert first is not None
    assert second is not None
    assert [msg.message_id for msg in first] == [1]
    assert [msg.message_id for msg in second] == [2]


@pytest.mark.asyncio
async def test_cancelled_owner_leaves_no_orphan_buffer() -> None:
    """Отмена владельца окна чистит буфер: следующий бурст не тянет чужие сообщения."""
    aggregator = InboxAggregator(window_s=_WINDOW_S)

    owner = asyncio.create_task(aggregator.collect(_KEY, _message(1)))
    await asyncio.sleep(0)
    owner.cancel()
    with pytest.raises(asyncio.CancelledError):
        await owner

    bundle = await aggregator.collect(_KEY, _message(2))

    assert bundle is not None
    assert [msg.message_id for msg in bundle] == [2]


@pytest.mark.asyncio
async def test_command_goes_straight_to_handler() -> None:
    """Команда минует агрегатор и попадает в handler как есть."""
    aggregator = _AggregatorSpy()
    middleware = _middleware(aggregator)
    command = _message(1, text="/start")

    result = await middleware(_HandlerSpy(), command, {})

    assert result == "handled"
    assert aggregator.calls == []


@pytest.mark.asyncio
async def test_non_content_message_goes_straight_to_handler() -> None:
    """Не-контентное сообщение (нет текста и вложений) идёт мимо агрегатора."""
    aggregator = _AggregatorSpy()
    middleware = _middleware(aggregator)
    handler = _HandlerSpy()
    sticker_message = _message(1, text=None)

    await middleware(handler, sticker_message, {})

    assert [event for event, _data in handler.calls] == [sticker_message]
    assert aggregator.calls == []


@pytest.mark.asyncio
async def test_content_without_from_user_dropped() -> None:
    """Контентное сообщение без from_user дропается до буферизации."""
    aggregator = _AggregatorSpy()
    middleware = _middleware(aggregator)
    handler = _HandlerSpy()

    await middleware(handler, _message(1, from_user_id=None), {})

    assert handler.calls == []
    assert aggregator.calls == []


@pytest.mark.asyncio
async def test_disallowed_user_dropped_before_buffering() -> None:
    """Запрещённый пользователь не набивает буфер и не доходит до handler'а."""
    aggregator = _AggregatorSpy()
    middleware = _middleware(aggregator, is_allowed=False)
    handler = _HandlerSpy()

    await middleware(handler, _message(1), {})

    assert handler.calls == []
    assert aggregator.calls == []


@pytest.mark.asyncio
async def test_disallowed_user_command_dropped() -> None:
    """Команда запрещённого пользователя не доходит до handler'а."""
    aggregator = _AggregatorSpy()
    middleware = _middleware(aggregator, is_allowed=False)
    handler = _HandlerSpy()

    await middleware(handler, _message(1, text="/start"), {})

    assert handler.calls == []
    assert aggregator.calls == []


@pytest.mark.asyncio
async def test_disallowed_user_unknown_command_dropped() -> None:
    """Неизвестная команда запрещённого пользователя не доходит до handler'а.

    Такой текст не матчится командными фильтрами и попадает в контентный
    catch-all — гейт обязан отсечь его до входа в handler.
    """
    aggregator = _AggregatorSpy()
    middleware = _middleware(aggregator, is_allowed=False)
    handler = _HandlerSpy()

    await middleware(handler, _message(1, text="/unknown"), {})

    assert handler.calls == []
    assert aggregator.calls == []


@pytest.mark.asyncio
async def test_command_without_from_user_dropped() -> None:
    """Команда без from_user дропается: проверить доступ не по чему."""
    aggregator = _AggregatorSpy()
    middleware = _middleware(aggregator)
    handler = _HandlerSpy()

    await middleware(handler, _message(1, from_user_id=None, text="/start"), {})

    assert handler.calls == []
    assert aggregator.calls == []


@pytest.mark.asyncio
async def test_buffered_message_does_not_reach_handler() -> None:
    """Сообщение, ушедшее в чужое окно (collect вернул None), handler не видит."""
    aggregator = _AggregatorSpy(bundle=None)
    middleware = _middleware(aggregator)
    handler = _HandlerSpy()

    await middleware(handler, _message(2), {})

    assert handler.calls == []
    assert [key for key, _msg in aggregator.calls] == [_KEY]


@pytest.mark.asyncio
async def test_bundle_passed_to_handler_with_anchor() -> None:
    """Собранный пакет уходит в контекст, handler зовётся с анкер-сообщением."""
    bundle = [_message(10), _message(20)]
    aggregator = _AggregatorSpy(bundle=bundle)
    middleware = _middleware(aggregator)
    handler = _HandlerSpy()

    await middleware(handler, _message(20), {})

    assert len(handler.calls) == 1
    event, data = handler.calls[0]
    assert cast(Message, event).message_id == 10
    assert data[INBOX_BUNDLE_KEY] == bundle
