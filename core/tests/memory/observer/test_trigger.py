"""Observer: триггер по порогу, advance watermark, идемпотентность, guard конкурентности."""

import asyncio
from typing import Any
from uuid import uuid4

import pytest

from bestfiend.memory.observer.schemas import Observation, ObserverOutput
from bestfiend.memory.observer.service import ObserverService
from tests.memory.fakes import (
    NoteRepositoryFake,
    OperationLogRepositoryFake,
    TransactionalDatabaseFake,
    TurnRepositoryFake,
    WatermarkRepositoryFake,
    build_observer_service,
    make_turn,
    stub_observer_llm,
)


_OUTPUT_ONE_OBS = ObserverOutput(
    observations=[Observation(content="решение принято", weight="high", subject="user")]
)


@pytest.mark.asyncio
async def test_below_threshold_no_run(
    observer_parts: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Порог не достигнут → LLM не вызывается, watermark на месте."""
    service: ObserverService = observer_parts["service"]
    observer_parts["turns"].turns = [make_turn(1, tokens=50)]  # 50 < threshold 100
    calls = stub_observer_llm(monkeypatch, _OUTPUT_ONE_OBS, db=observer_parts["db"])

    await service.maybe_run(uuid4())

    assert calls == []
    assert observer_parts["watermarks"].positions == {}


@pytest.mark.asyncio
async def test_threshold_reached_runs_and_advances(
    observer_parts: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Порог достигнут → один прогон, заметки вставлены, watermark = последний ход."""
    service: ObserverService = observer_parts["service"]
    calls = stub_observer_llm(monkeypatch, _OUTPUT_ONE_OBS, db=observer_parts["db"])

    await service.maybe_run(uuid4())

    assert len(calls) == 1
    assert len(observer_parts["notes"].inserted) == 1
    draft = observer_parts["notes"].inserted[0]
    assert draft.kind == "observation"
    assert draft.in_journal is True
    assert draft.source_turn_start == 1
    assert draft.source_turn_end == 2
    assert observer_parts["watermarks"].positions["observer"] == 2


@pytest.mark.asyncio
async def test_idempotent_after_advance(
    observer_parts: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Повторный maybe_run после advance — no-op (необработанного меньше порога)."""
    service: ObserverService = observer_parts["service"]
    calls = stub_observer_llm(monkeypatch, _OUTPUT_ONE_OBS, db=observer_parts["db"])
    user_id = uuid4()

    await service.maybe_run(user_id)
    await service.maybe_run(user_id)

    assert len(calls) == 1


@pytest.mark.asyncio
async def test_llm_failure_keeps_watermark(
    observer_parts: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Сбой LLM → watermark не двигается, прогон повторится на следующем триггере."""

    async def failing_invoke(*args: Any, **kwargs: Any) -> None:
        return None  # контракт invoke_structured: сбой → None

    monkeypatch.setattr(
        "bestfiend.memory.observer.service.invoke_structured", failing_invoke
    )
    service: ObserverService = observer_parts["service"]

    await service.maybe_run(uuid4())

    assert observer_parts["watermarks"].positions == {}
    assert observer_parts["notes"].inserted == []


@pytest.mark.asyncio
async def test_concurrent_runs_guarded(monkeypatch: pytest.MonkeyPatch) -> None:
    """Два конкурентных maybe_run одного user → ровно один прогон (guard)."""
    turns = TurnRepositoryFake([make_turn(1), make_turn(2)], delay_s=0.05)
    service = build_observer_service(turns=turns, notes=NoteRepositoryFake())
    calls = stub_observer_llm(monkeypatch, _OUTPUT_ONE_OBS)
    user_id = uuid4()

    await asyncio.gather(service.maybe_run(user_id), service.maybe_run(user_id))

    assert len(calls) == 1


@pytest.mark.asyncio
async def test_disabled_without_llm_config(observer_parts: dict[str, Any]) -> None:
    """Без llm_config Observer выключен: maybe_run — мгновенный no-op."""
    turns: TurnRepositoryFake = observer_parts["turns"]
    service = build_observer_service(
        turns=turns,
        notes=observer_parts["notes"],
        entities=observer_parts["entities"],
        watermarks=observer_parts["watermarks"],
        llm_enabled=False,
    )

    await service.maybe_run(uuid4())

    assert service.is_enabled is False
    assert turns.token_sum_calls == 0


@pytest.mark.asyncio
async def test_persist_is_single_transaction(
    observer_parts: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Insert, бюджет профиля, ops-лог и advance идут через один executor одной транзакции.

    Журнал в бюджете → консолидация не открывает вторую транзакцию;
    его перечитка живёт вне транзакции персиста.
    """
    service: ObserverService = observer_parts["service"]
    db: TransactionalDatabaseFake = observer_parts["db"]
    stub_observer_llm(monkeypatch, _OUTPUT_ONE_OBS, db=db)
    notes: NoteRepositoryFake = observer_parts["notes"]
    watermarks: WatermarkRepositoryFake = observer_parts["watermarks"]
    ops: OperationLogRepositoryFake = observer_parts["ops"]

    await service.maybe_run(uuid4())

    assert len(db.transactions) == 1
    tx = db.transactions[0]
    assert tx.committed
    assert notes.insert_executors == [tx]
    assert notes.pinned_executors == [tx]  # бюджет профиля — в той же tx
    assert ops.log_executors == [tx]
    assert watermarks.advance_executors == [tx]
    assert tx not in notes.journal_executors  # бюджет журнала — вне транзакции
    assert [op.op for op in ops.logged] == ["add"]  # след наблюдения в ops-логе


@pytest.mark.asyncio
async def test_failed_persist_rolls_back_and_retry_has_no_duplicates(
    observer_parts: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Сбой advance откатывает заметки и ops; ретрай вставляет батч ровно один раз."""
    service: ObserverService = observer_parts["service"]
    db: TransactionalDatabaseFake = observer_parts["db"]
    stub_observer_llm(monkeypatch, _OUTPUT_ONE_OBS, db=db)
    notes: NoteRepositoryFake = observer_parts["notes"]
    watermarks: WatermarkRepositoryFake = observer_parts["watermarks"]
    ops: OperationLogRepositoryFake = observer_parts["ops"]
    watermarks.fail_advances_left = 1
    user_id = uuid4()

    with pytest.raises(RuntimeError):
        await service.maybe_run(user_id)

    assert db.transactions[0].rolled_back
    assert notes.inserted == []  # откат: заметки не видны
    assert ops.logged == []  # откат: следы операций не видны
    assert watermarks.positions == {}

    await service.maybe_run(user_id)  # ретрай после восстановления

    assert db.transactions[1].committed
    assert len(notes.inserted) == 1  # без дублей
    assert watermarks.positions["observer"] == 2
