"""SleepTimeScheduler — idle-триггер: N минут тишины → цикл консолидации.

Таймер per-user, сбрасывается каждым write (touch). Живёт в процессе:
рестарт теряет таймеры — цикл отложится до следующего сообщения
(осознанный трейд-офф против внешнего шедулера).
"""

import asyncio
from uuid import UUID

from loguru import logger

from bestfiend.memory.sleep_time.service import SleepTimeService


class SleepTimeScheduler:
    """Реестр per-user idle-таймеров."""

    __slots__ = ("_idle_seconds", "_service", "_timers")

    def __init__(self, service: SleepTimeService, *, idle_seconds: float) -> None:
        self._service = service
        self._idle_seconds = idle_seconds
        self._timers: dict[UUID, asyncio.Task[None]] = {}

    def touch(self, user_id: UUID) -> None:
        """Сбрасывает таймер пользователя: цикл выполнится после N тишины."""
        previous = self._timers.pop(user_id, None)
        if previous is not None:
            previous.cancel()
        timer = asyncio.get_running_loop().create_task(self._wait_and_run(user_id))
        self._timers[user_id] = timer
        timer.add_done_callback(lambda t: self._forget(user_id, t))

    async def stop(self) -> None:
        """Отменяет и дожидается всех таймеров при shutdown."""
        timers = list(self._timers.values())
        for timer in timers:
            timer.cancel()
        self._timers.clear()
        if timers:
            await asyncio.gather(*timers, return_exceptions=True)

    async def _wait_and_run(self, user_id: UUID) -> None:
        await asyncio.sleep(self._idle_seconds)
        try:
            await self._service.run_cycle(user_id)
        except Exception as exc:  # noqa: BLE001 — фоновый цикл не валит процесс
            logger.warning("sleep scheduler: cycle failed user_id={}: {}", user_id, exc)

    def _forget(self, user_id: UUID, timer: asyncio.Task[None]) -> None:
        """Чистит реестр, не задевая более свежий таймер того же пользователя."""
        if self._timers.get(user_id) is timer:
            self._timers.pop(user_id, None)
