"""Карточки сущностей: генерация, supersede прежней, span-провенанс, cap, fail-soft."""

import dataclasses
from typing import Any
from uuid import uuid4

import pytest

from bestfiend.memory.settings import MemorySettings
from bestfiend.memory.sleep_time.entity_cards import run_entity_cards
from bestfiend.memory.sleep_time.entity_cards import service as entity_cards
from bestfiend.memory.sleep_time.entity_cards.schemas import EntityCardOutput
from tests.memory.fakes import (
    EntityRepositoryFake,
    NoteRepositoryFake,
    OperationLogRepositoryFake,
    TransactionalDatabaseFake,
    make_note,
)

from .conftest import make_ctx


def _stub_llm(
    monkeypatch: pytest.MonkeyPatch,
    output: EntityCardOutput | None,
    *,
    db: TransactionalDatabaseFake | None = None,
) -> list[Any]:
    """Подменяет invoke_structured модуля карточек; ассертит вызов вне транзакции."""
    calls: list[Any] = []

    async def fake_invoke(ctx: Any, schema: Any, messages: Any, **kwargs: Any) -> Any:
        if db is not None:
            assert db.in_transaction is False
        calls.append(messages)
        return output

    monkeypatch.setattr(entity_cards, "invoke_structured", fake_invoke)
    return calls


@pytest.mark.asyncio
async def test_card_generated_with_span_and_ops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Первая карточка: insert kind='entity_card' + тег + min/max span + ops add."""
    entity_id = uuid4()
    entities = EntityRepositoryFake({"BestFiend": entity_id})
    sources = [
        dataclasses.replace(
            make_note("заметка о проекте"), source_turn_start=5, source_turn_end=8
        ),
        dataclasses.replace(
            make_note("ещё заметка"), source_turn_start=12, source_turn_end=14
        ),
    ]
    notes = NoteRepositoryFake()
    notes.hot_entities = [entity_id]
    notes.by_entity = {entity_id: sources}
    ops = OperationLogRepositoryFake()
    db = TransactionalDatabaseFake()
    ctx = make_ctx(notes=notes, entities=entities, ops=ops, db=db)
    _stub_llm(monkeypatch, EntityCardOutput(content="Досье BestFiend: ..."), db=db)

    await run_entity_cards(uuid4(), ctx)

    [(draft, card_id)] = notes.inserted_with_ids
    assert draft.kind == "entity_card"
    assert draft.entity_ids == (entity_id,)
    assert draft.source_turn_start == 5  # min по источникам
    assert draft.source_turn_end == 14  # max по источникам
    assert notes.superseded == []  # прежней карточки не было
    add_ops = ops.logged_ops("add")
    assert add_ops[0].note_id == card_id
    assert "BestFiend" in (add_ops[0].detail or "")
    assert db.transactions[0].committed


@pytest.mark.asyncio
async def test_regeneration_supersedes_previous_card(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Прежняя карточка вытесняется новой тем же executor; ops supersede."""
    entity_id = uuid4()
    previous = make_note("старое досье", kind="entity_card")
    entities = EntityRepositoryFake({"BestFiend": entity_id})
    notes = NoteRepositoryFake()
    notes.hot_entities = [entity_id]
    notes.by_entity = {entity_id: [make_note("свежая заметка")]}
    notes.active_cards = {entity_id: previous}
    ops = OperationLogRepositoryFake()
    db = TransactionalDatabaseFake()
    ctx = make_ctx(notes=notes, entities=entities, ops=ops, db=db)
    _stub_llm(monkeypatch, EntityCardOutput(content="Досье v2"), db=db)

    await run_entity_cards(uuid4(), ctx)

    [(_, card_id)] = notes.inserted_with_ids
    assert notes.superseded == [(previous.id, card_id)]
    assert notes.supersede_executors == [db.transactions[0]]
    supersede_ops = ops.logged_ops("supersede")
    assert supersede_ops[0].target_note_id == previous.id


@pytest.mark.asyncio
async def test_no_hot_entities_no_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    """Нет горячих сущностей → ноль LLM-вызовов и записей."""
    notes = NoteRepositoryFake()
    ctx = make_ctx(notes=notes)
    calls = _stub_llm(monkeypatch, EntityCardOutput(content="x"))

    await run_entity_cards(uuid4(), ctx)

    assert calls == []
    assert notes.inserted == []


@pytest.mark.asyncio
async def test_card_failure_does_not_break_others(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Сбой LLM одной карточки → остальные сущности обработаны."""
    first, second = uuid4(), uuid4()
    entities = EntityRepositoryFake({"First": first, "Second": second})
    notes = NoteRepositoryFake()
    notes.hot_entities = [first, second]
    notes.by_entity = {
        first: [make_note("про first")],
        second: [make_note("про second")],
    }
    ctx = make_ctx(notes=notes, entities=entities)
    outputs: list[EntityCardOutput | None] = [None, EntityCardOutput(content="Досье")]

    async def flaky_invoke(ctx: Any, schema: Any, messages: Any, **kwargs: Any) -> Any:
        return outputs.pop(0)

    monkeypatch.setattr(entity_cards, "invoke_structured", flaky_invoke)

    await run_entity_cards(uuid4(), ctx)

    assert len(notes.inserted) == 1  # вторая карточка записана несмотря на сбой первой


@pytest.mark.asyncio
async def test_cap_limits_cards_per_cycle(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cap на цикл режет список горячих сущностей (лимит уходит в выборку)."""
    ids = [uuid4() for _ in range(5)]
    entities = EntityRepositoryFake({f"E{i}": eid for i, eid in enumerate(ids)})
    notes = NoteRepositoryFake()
    notes.hot_entities = ids
    notes.by_entity = {eid: [make_note(f"заметка {i}")] for i, eid in enumerate(ids)}
    settings = MemorySettings(sleep_max_cards_per_cycle=2)
    ctx = make_ctx(notes=notes, entities=entities, settings=settings)
    calls = _stub_llm(monkeypatch, EntityCardOutput(content="Досье"))

    await run_entity_cards(uuid4(), ctx)

    assert len(calls) == 2
    assert len(notes.inserted) == 2
