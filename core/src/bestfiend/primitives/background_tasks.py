"""Управление фоновыми задачами процесса."""

import asyncio
from collections.abc import Coroutine
from typing import Any

from loguru import logger


class BackgroundTaskSupervisorClosedError(RuntimeError):
    """Supervisor больше не принимает новые задачи."""


class BackgroundTaskSupervisor:
    """Владеет фоновыми задачами и завершает их при shutdown."""

    def __init__(self) -> None:
        """Создать supervisor, открытый для новых задач."""
        self._tasks: set[asyncio.Task[None]] = set()
        self._accepting_tasks = True

    def create_task(
        self,
        coroutine: Coroutine[Any, Any, None],
        *,
        name: str,
    ) -> asyncio.Task[None]:
        """Запустить и зарегистрировать фоновую корутину."""
        if not self._accepting_tasks:
            coroutine.close()
            raise BackgroundTaskSupervisorClosedError(
                "supervisor фоновых задач уже остановлен"
            )
        task = asyncio.create_task(coroutine, name=name)
        self._tasks.add(task)
        task.add_done_callback(self._on_task_done)
        return task

    async def shutdown(self, *, timeout_s: float) -> None:
        """Дождаться задач, затем отменить оставшиеся после таймаута."""
        self._accepting_tasks = False
        tasks = set(self._tasks)
        if not tasks:
            return
        _, pending = await asyncio.wait(tasks, timeout=timeout_s)
        if not pending:
            return
        logger.warning(
            "BackgroundTaskSupervisor: отмена {} задач после таймаута {} с",
            len(pending),
            timeout_s,
        )
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)

    def _on_task_done(self, task: asyncio.Task[None]) -> None:
        """Убрать завершённую задачу и записать её исключение."""
        self._tasks.discard(task)
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            logger.warning(
                "BackgroundTaskSupervisor: задача {} завершилась с ошибкой: {}",
                task.get_name(),
                error,
            )
