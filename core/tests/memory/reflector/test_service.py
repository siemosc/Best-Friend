"""Reflector: двухфазная консолидация — LLM вне транзакции, короткий apply."""

import dataclasses
from typing import Any
from uuid import uuid4

import pytest

from bestfiend.memory.reflector.schemas import Reflection, ReflectorOutput
from bestfiend.memory.reflector.service import ReflectorService
from tests.memory.fakes import (
    NoteRepositoryFake,
    OperationLogRepositoryFake,
    TransactionalDatabaseFake,
    make_journal_note,
)


def _service(
    notes: NoteRepositoryFake,
    ops: OperationLogRepositoryFake,
    db: TransactionalDatabaseFake,
) -> ReflectorService:
    return ReflectorService(
        db=db,  # type: ignore[arg-type] — стаб по контракту
        notes_repository=notes,  # type: ignore[arg-type]
        ops_repository=ops,  # type: ignore[arg-type]
        llm_config={"provider": "openrouter", "model": "stub"},
        embedder=None,
    )


def _stub_llm(
    monkeypatch: pytest.MonkeyPatch,
    output: ReflectorOutput | None,
    *,
    db: TransactionalDatabaseFake | None = None,
) -> list[int]:
    """Подменяет модульный invoke_structured; ассертит вызов вне транзакции."""
    calls: list[int] = []

    async def fake_invoke(*args: Any, **kwargs: Any) -> ReflectorOutput | None:
        if db is not None:
            assert db.in_transaction is False  # LLM никогда не внутри транзакции
        calls.append(1)
        return output

    monkeypatch.setattr(
        "bestfiend.memory.reflector.service.invoke_structured", fake_invoke
    )
    return calls


@pytest.mark.asyncio
async def test_consolidate_applies_plan_atomically(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reflections вставлены, source+evict сняты, ops-след — одной транзакцией."""
    journal = [
        dataclasses.replace(
            make_journal_note(f"строка {i}"),
            source_turn_start=i + 1,
            source_turn_end=i + 2,
        )
        for i in range(4)
    ]
    notes = NoteRepositoryFake(journal=journal)
    ops = OperationLogRepositoryFake()
    db = TransactionalDatabaseFake()
    output = ReflectorOutput(
        reflections=[
            Reflection(content="сводная запись", source_indexes=[0, 1], weight="high")
        ],
        evict_indexes=[2],
    )
    _stub_llm(monkeypatch, output, db=db)

    applied = await _service(notes, ops, db).consolidate(uuid4(), journal)

    assert applied is True
    assert len(db.transactions) == 1
    tx = db.transactions[0]
    assert tx.committed
    [(draft, reflection_id)] = notes.inserted_with_ids
    assert draft.kind == "reflection"
    assert draft.in_journal is True
    assert draft.journal_weight == 2  # high
    assert draft.source_turn_start == 1  # min span источников
    assert draft.source_turn_end == 3  # max span источников
    # Сняты source (0,1) и evict (2); строка 3 осталась.
    assert set(notes.evicted_ids) == {journal[0].id, journal[1].id, journal[2].id}
    assert notes.insert_executors == [tx]
    assert notes.evict_executors == [tx]
    reflect_ops = ops.logged_ops("reflect")
    assert [op.note_id for op in reflect_ops] == [reflection_id]
    evict_ops = ops.logged_ops("evict")
    # Свёрнутые строки ссылаются на сводную, чистый evict — без ссылки.
    by_note = {op.note_id: op.target_note_id for op in evict_ops}
    assert by_note[journal[0].id] == reflection_id
    assert by_note[journal[1].id] == reflection_id
    assert by_note[journal[2].id] is None


@pytest.mark.asyncio
async def test_llm_failure_returns_false_without_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Сбой LLM → False, никаких транзакций и записей (вызывающий делает FIFO)."""
    notes = NoteRepositoryFake(journal=[make_journal_note("строка")])
    ops = OperationLogRepositoryFake()
    db = TransactionalDatabaseFake()
    _stub_llm(monkeypatch, None, db=db)

    applied = await _service(notes, ops, db).consolidate(uuid4(), notes.journal)

    assert applied is False
    assert db.transactions == []
    assert notes.inserted == []


@pytest.mark.asyncio
async def test_empty_output_returns_false(monkeypatch: pytest.MonkeyPatch) -> None:
    """Пустой выход LLM (нечего сворачивать) → False без транзакций."""
    notes = NoteRepositoryFake(journal=[make_journal_note("строка")])
    db = TransactionalDatabaseFake()
    _stub_llm(monkeypatch, ReflectorOutput(), db=db)

    applied = await _service(notes, OperationLogRepositoryFake(), db).consolidate(
        uuid4(), notes.journal
    )

    assert applied is False
    assert db.transactions == []


@pytest.mark.asyncio
async def test_apply_failure_rolls_back_and_returns_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Сбой apply-транзакции → откат, False (вызывающий делает FIFO)."""
    journal = [make_journal_note("строка 0"), make_journal_note("строка 1")]
    notes = NoteRepositoryFake(journal=journal)
    ops = OperationLogRepositoryFake()
    db = TransactionalDatabaseFake()
    output = ReflectorOutput(
        reflections=[Reflection(content="сводная", source_indexes=[0], weight="mid")]
    )
    _stub_llm(monkeypatch, output, db=db)

    async def failing_evict(note_ids: Any, *, executor: Any = None) -> None:
        raise RuntimeError("db down (simulated)")

    notes.evict_from_journal = failing_evict  # type: ignore[method-assign]

    applied = await _service(notes, ops, db).consolidate(uuid4(), journal)

    assert applied is False
    assert db.transactions[0].rolled_back
    assert notes.inserted == []  # вставка откатилась
    assert ops.logged == []


@pytest.mark.asyncio
async def test_out_of_range_indexes_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    """Индексы вне диапазона журнала игнорируются, валидная часть применяется."""
    journal = [make_journal_note("строка 0"), make_journal_note("строка 1")]
    notes = NoteRepositoryFake(journal=journal)
    db = TransactionalDatabaseFake()
    output = ReflectorOutput(
        reflections=[
            Reflection(content="сводная", source_indexes=[0, 99], weight="mid")
        ],
        evict_indexes=[100],
    )
    _stub_llm(monkeypatch, output, db=db)

    applied = await _service(notes, OperationLogRepositoryFake(), db).consolidate(
        uuid4(), journal
    )

    assert applied is True
    assert notes.evicted_ids == [journal[0].id]  # только валидный source
