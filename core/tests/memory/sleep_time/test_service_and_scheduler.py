"""Цикл sleep-time: fail-soft задач, общий guard с Observer, idle-шедулер."""

import asyncio
from typing import Any
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

from bestfiend.memory.locks import MemoryLocks
from bestfiend.memory.settings import MemorySettings
from bestfiend.memory.sleep_time import service as service_module
from bestfiend.memory.sleep_time.scheduler import SleepTimeScheduler
from bestfiend.memory.sleep_time.service import SleepTimeService
from tests.memory.fakes import (
    EntityRepositoryFake,
    NoteRepositoryFake,
    OperationLogRepositoryFake,
    TransactionalDatabaseFake,
)


def _service(locks: MemoryLocks | None = None) -> SleepTimeService:
    return SleepTimeService(
        db=TransactionalDatabaseFake(),  # type: ignore[arg-type] — стаб по контракту
        notes_repository=NoteRepositoryFake(),  # type: ignore[arg-type]
        entities_repository=EntityRepositoryFake(),  # type: ignore[arg-type]
        ops_repository=OperationLogRepositoryFake(),  # type: ignore[arg-type]
        probes_repository=AsyncMock(),
        measurements_repository=AsyncMock(),
        settings=MemorySettings(),
        llm_config={"provider": "openrouter", "model": "stub"},
        embedder=None,
        locks=locks or MemoryLocks(),
    )


def _patch_tasks(
    monkeypatch: pytest.MonkeyPatch, *, failing: frozenset[str] = frozenset()
) -> dict[str, int]:
    """Подменяет задачи цикла счётчиками; перечисленные в failing — бросают."""
    counters: dict[str, int] = {"cards": 0, "summaries": 0, "merge": 0, "probes": 0}

    def make_task(name: str) -> Any:
        async def task(user_id: UUID, ctx: Any, *args: Any, **kwargs: Any) -> None:
            counters[name] += 1
            if name in failing:
                raise RuntimeError(f"{name} failed (simulated)")

        return task

    monkeypatch.setattr(service_module, "run_entity_cards", make_task("cards"))
    monkeypatch.setattr(service_module, "run_period_summaries", make_task("summaries"))
    monkeypatch.setattr(service_module, "run_duplicate_merge", make_task("merge"))
    monkeypatch.setattr(service_module, "run_probes", make_task("probes"))
    return counters


@pytest.mark.asyncio
async def test_cycle_runs_all_tasks_in_order(monkeypatch: pytest.MonkeyPatch) -> None:
    """Цикл выполняет все четыре задачи."""
    counters = _patch_tasks(monkeypatch)

    await _service().run_cycle(uuid4())

    assert counters == {"cards": 1, "summaries": 1, "merge": 1, "probes": 1}


@pytest.mark.asyncio
async def test_task_failure_does_not_stop_cycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Сбой задачи (карточек) → следующие задачи выполнены."""
    counters = _patch_tasks(monkeypatch, failing=frozenset({"cards"}))

    await _service().run_cycle(uuid4())

    assert counters["cards"] == 1
    assert counters["summaries"] == 1
    assert counters["merge"] == 1
    assert counters["probes"] == 1


@pytest.mark.asyncio
async def test_cycle_holds_shared_lock_blocking_observer_style_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Во время цикла общий лок занят: try_hold уходит с False (Observer-путь)."""
    locks = MemoryLocks()
    user_id = uuid4()
    lock_states: list[bool] = []

    async def probing_task(uid: UUID, ctx: Any, *args: Any, **kwargs: Any) -> None:
        async with locks.try_hold(uid) as acquired:
            lock_states.append(acquired)

    monkeypatch.setattr(service_module, "run_entity_cards", probing_task)

    async def noop(uid: UUID, ctx: Any, *args: Any, **kwargs: Any) -> None:
        return None

    monkeypatch.setattr(service_module, "run_period_summaries", noop)
    monkeypatch.setattr(service_module, "run_duplicate_merge", noop)
    monkeypatch.setattr(service_module, "run_probes", noop)

    await _service(locks).run_cycle(user_id)

    assert lock_states == [False]  # лок держит цикл — конкурент не входит
    assert locks._claims.get(user_id, 0) == 0  # после цикла отпущен


@pytest.mark.asyncio
async def test_scheduler_runs_after_idle_and_resets_on_touch() -> None:
    """touch ставит таймер; повторный touch сбрасывает; после idle — один цикл."""
    runs: list[UUID] = []

    class FakeService:
        async def run_cycle(self, user_id: UUID) -> None:
            runs.append(user_id)

    scheduler = SleepTimeScheduler(FakeService(), idle_seconds=0.03)  # type: ignore[arg-type]
    user_id = uuid4()

    scheduler.touch(user_id)
    await asyncio.sleep(0.01)
    scheduler.touch(user_id)  # сброс: первый таймер не дойдёт до цикла
    await asyncio.sleep(0.06)

    assert runs == [user_id]


@pytest.mark.asyncio
async def test_scheduler_stop_cancels_timers() -> None:
    """stop() гасит таймеры — цикл не выполняется."""
    runs: list[UUID] = []

    class FakeService:
        async def run_cycle(self, user_id: UUID) -> None:
            runs.append(user_id)

    scheduler = SleepTimeScheduler(FakeService(), idle_seconds=0.02)  # type: ignore[arg-type]
    scheduler.touch(uuid4())
    await scheduler.stop()
    await asyncio.sleep(0.05)

    assert runs == []
