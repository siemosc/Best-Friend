"""Тесты supervisor фоновых задач."""

import asyncio

import pytest

from bestfiend.primitives.background_tasks import (
    BackgroundTaskSupervisor,
    BackgroundTaskSupervisorClosedError,
)


@pytest.mark.asyncio
async def test_shutdown_waits_for_active_task() -> None:
    """Shutdown должен дождаться задачи в пределах таймаута."""
    supervisor = BackgroundTaskSupervisor()
    completed = asyncio.Event()

    async def work() -> None:
        """Отметить нормальное завершение задачи."""
        await asyncio.sleep(0)
        completed.set()

    supervisor.create_task(work(), name="test-work")

    await supervisor.shutdown(timeout_s=1)

    assert completed.is_set()


@pytest.mark.asyncio
async def test_shutdown_cancels_task_after_timeout() -> None:
    """Зависшая задача должна отменяться после таймаута."""
    supervisor = BackgroundTaskSupervisor()
    cancelled = asyncio.Event()

    async def work() -> None:
        """Зафиксировать получение отмены."""
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    supervisor.create_task(work(), name="test-stuck-work")

    await supervisor.shutdown(timeout_s=0)

    assert cancelled.is_set()


@pytest.mark.asyncio
async def test_supervisor_rejects_task_after_shutdown() -> None:
    """Остановленный supervisor не должен принимать новую работу."""
    supervisor = BackgroundTaskSupervisor()
    await supervisor.shutdown(timeout_s=0)

    async def work() -> None:
        """Пустая тестовая задача."""

    with pytest.raises(BackgroundTaskSupervisorClosedError):
        supervisor.create_task(work(), name="late-work")
