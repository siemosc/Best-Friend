"""MemoryLocks: атомарный try_hold без очереди, blocking hold дожидается."""

import asyncio
from uuid import uuid4

import pytest

from bestfiend.memory.locks import MemoryLocks


@pytest.mark.asyncio
async def test_try_hold_acquires_when_free() -> None:
    """Свободный лок захватывается, второй пользователь независим."""
    locks = MemoryLocks()
    user_a, user_b = uuid4(), uuid4()

    async with locks.try_hold(user_a) as acquired_a:
        assert acquired_a is True
        async with locks.try_hold(user_b) as acquired_b:
            assert acquired_b is True  # лок per-user, не глобальный


@pytest.mark.asyncio
async def test_try_hold_does_not_queue_when_busy() -> None:
    """Занятый лок → try_hold выходит сразу с False, не дожидаясь."""
    locks = MemoryLocks()
    user_id = uuid4()

    async with locks.try_hold(user_id) as outer:
        assert outer is True
        async with locks.try_hold(user_id) as inner:
            assert inner is False  # без ожидания и без работы


@pytest.mark.asyncio
async def test_try_hold_skips_when_blocking_waiter_queued() -> None:
    """Очередь из blocking hold → try_hold выходит сразу с False, не вставая за ним.

    Регрессия: голый asyncio.Lock.acquire() на «свободном» локе с живыми
    waiters встаёт в очередь — Observer повис бы за ожидающим sleep-циклом.
    """
    locks = MemoryLocks()
    user_id = uuid4()
    release_holder = asyncio.Event()
    waiter_entered = asyncio.Event()

    async def holder() -> None:
        async with locks.try_hold(user_id) as acquired:
            assert acquired
            await release_holder.wait()

    async def blocking_waiter() -> None:
        async with locks.hold(user_id):
            waiter_entered.set()

    holder_task = asyncio.create_task(holder())
    await asyncio.sleep(0.005)  # holder держит лок
    waiter_task = asyncio.create_task(blocking_waiter())
    await asyncio.sleep(0.005)  # waiter встал в очередь hold

    async with locks.try_hold(user_id) as acquired:
        assert acquired is False  # мгновенный отказ: держатель + очередь

    release_holder.set()
    await asyncio.gather(holder_task, waiter_task)
    assert waiter_entered.is_set()  # blocking-очередь дождалась своего
    assert locks._claims.get(user_id, 0) == 0


@pytest.mark.asyncio
async def test_hold_waits_for_release() -> None:
    """Blocking hold дожидается освобождения и выполняет работу."""
    locks = MemoryLocks()
    user_id = uuid4()
    order: list[str] = []

    async def holder() -> None:
        async with locks.try_hold(user_id) as acquired:
            assert acquired
            order.append("observer start")
            await asyncio.sleep(0.02)
            order.append("observer end")

    async def waiter() -> None:
        await asyncio.sleep(0.005)  # стартуем, когда лок уже занят
        async with locks.hold(user_id):
            order.append("sleep cycle")

    await asyncio.gather(holder(), waiter())

    assert order == ["observer start", "observer end", "sleep cycle"]
    assert locks._claims.get(user_id, 0) == 0
