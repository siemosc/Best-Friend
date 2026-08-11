"""Слияние почти-дублей: батч, дизъюнктность, revalidation, наследование, ops."""

import dataclasses
from typing import Any
from uuid import uuid4

import pytest

from bestfiend.memory.sleep_time.duplicate_merge import run_duplicate_merge
from bestfiend.memory.sleep_time.duplicate_merge import service as duplicate_merge
from bestfiend.memory.sleep_time.duplicate_merge.schemas import (
    MergeDecision,
    MergeOutput,
)
from tests.memory.fakes import (
    NoteRepositoryFake,
    OperationLogRepositoryFake,
    TransactionalDatabaseFake,
    make_note,
)

from .conftest import make_ctx


def _stub_llm(
    monkeypatch: pytest.MonkeyPatch,
    output: MergeOutput | None,
    *,
    db: TransactionalDatabaseFake | None = None,
) -> list[Any]:
    calls: list[Any] = []

    async def fake_invoke(ctx: Any, schema: Any, messages: Any, **kwargs: Any) -> Any:
        if db is not None:
            assert db.in_transaction is False
        calls.append(messages)
        return output

    monkeypatch.setattr(duplicate_merge, "invoke_structured", fake_invoke)
    return calls


@pytest.mark.asyncio
async def test_merge_supersedes_both_with_inheritance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """merge=true: новая заметка, обе superseded, pin/теги/span унаследованы, ops×2."""
    tag_left, tag_right = uuid4(), uuid4()
    left = dataclasses.replace(
        make_note(
            "любит чай",
            kind="preference",
            subject="user",
            pinned=True,
            pin_section="preferences",
        ),
        source_turn_start=3,
        source_turn_end=5,
    )
    right = dataclasses.replace(
        make_note("предпочитает чай кофе", kind="preference", subject="user"),
        source_turn_start=9,
        source_turn_end=11,
    )
    notes = NoteRepositoryFake(entity_tags={left.id: [tag_left], right.id: [tag_right]})
    notes.near_duplicates = [(left, right, 0.95)]
    ops = OperationLogRepositoryFake()
    db = TransactionalDatabaseFake()
    ctx = make_ctx(notes=notes, ops=ops, db=db)
    _stub_llm(
        monkeypatch,
        MergeOutput(
            decisions=[
                MergeDecision(
                    pair_index=0, merge=True, merged_content="любит чай, не кофе"
                )
            ]
        ),
        db=db,
    )

    await run_duplicate_merge(uuid4(), ctx)

    [(draft, merged_id)] = notes.inserted_with_ids
    assert draft.kind == "preference"
    assert draft.subject == "user"  # общий субъект родителей унаследован
    assert draft.pinned is True  # pinned-родитель передал pin
    assert draft.pin_section == "preferences"
    assert set(draft.entity_ids) == {tag_left, tag_right}  # объединение тегов
    assert draft.source_turn_start == 3  # min span пары
    assert draft.source_turn_end == 11  # max span пары
    assert set(notes.superseded) == {(left.id, merged_id), (right.id, merged_id)}
    merge_ops = ops.logged_ops("merge")
    assert {op.target_note_id for op in merge_ops} == {left.id, right.id}
    assert all(op.note_id == merged_id for op in merge_ops)


@pytest.mark.asyncio
async def test_merge_subject_disagreement_falls_back_to_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """fact-пара с разными субъектами → merged-заметка без субъекта (нет уверенности)."""
    left = make_note("у пользователя свой сервер", kind="fact", subject="user")
    right = make_note("сервер живёт в подвале", kind="fact", subject="world")
    notes = NoteRepositoryFake()
    notes.near_duplicates = [(left, right, 0.95)]
    ctx = make_ctx(notes=notes, db=TransactionalDatabaseFake())
    _stub_llm(
        monkeypatch,
        MergeOutput(
            decisions=[
                MergeDecision(
                    pair_index=0,
                    merge=True,
                    merged_content="свой сервер пользователя живёт в подвале",
                )
            ]
        ),
    )

    await run_duplicate_merge(uuid4(), ctx)

    [draft] = notes.inserted
    assert draft.subject is None


@pytest.mark.asyncio
async def test_overlapping_pairs_collapsed_to_disjoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Пересечения самоджойна (A-B, A-C) → greedy оставляет сильнейшую пару."""
    a = make_note("факт А", kind="fact")
    b = make_note("факт Б", kind="fact")
    c = make_note("факт В", kind="fact")
    notes = NoteRepositoryFake()
    notes.near_duplicates = [(a, b, 0.97), (a, c, 0.94)]  # сильнейшая первой
    db = TransactionalDatabaseFake()
    ctx = make_ctx(notes=notes, db=db)
    captured: dict[str, Any] = {}

    async def capture_invoke(
        ctx: Any, schema: Any, messages: Any, **kwargs: Any
    ) -> Any:
        captured["prompt"] = messages[1].content
        return MergeOutput()

    monkeypatch.setattr(duplicate_merge, "invoke_structured", capture_invoke)

    await run_duplicate_merge(uuid4(), ctx)

    assert "Пара 0" in captured["prompt"]
    assert "Пара 1" not in captured["prompt"]  # A-C отброшена (A уже занята)
    assert "факт В" not in captured["prompt"]


@pytest.mark.asyncio
async def test_revalidation_skips_stale_pair(monkeypatch: pytest.MonkeyPatch) -> None:
    """Заметка пары не active при revalidation → пара скипнута без записей."""
    left = make_note("дубль раз", kind="fact")
    right = make_note("дубль два", kind="fact")
    notes = NoteRepositoryFake()
    notes.near_duplicates = [(left, right, 0.95)]
    notes.statuses_map = {left.id: "superseded"}  # другая операция успела раньше
    ops = OperationLogRepositoryFake()
    db = TransactionalDatabaseFake()
    ctx = make_ctx(notes=notes, ops=ops, db=db)
    _stub_llm(
        monkeypatch,
        MergeOutput(
            decisions=[MergeDecision(pair_index=0, merge=True, merged_content="дубль")]
        ),
        db=db,
    )

    await run_duplicate_merge(uuid4(), ctx)

    assert notes.inserted == []  # вставка не дошла до коммита смысла
    assert notes.superseded == []
    assert ops.logged == []
    assert notes.statuses_executors == [db.transactions[0]]  # проверка — в транзакции


@pytest.mark.asyncio
async def test_merge_false_and_invalid_index_do_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """merge=false и pair_index вне диапазона → никаких записей."""
    left = make_note("раз", kind="fact")
    right = make_note("два", kind="fact")
    notes = NoteRepositoryFake()
    notes.near_duplicates = [(left, right, 0.93)]
    ctx = make_ctx(notes=notes)
    _stub_llm(
        monkeypatch,
        MergeOutput(
            decisions=[
                MergeDecision(pair_index=0, merge=False),
                MergeDecision(pair_index=9, merge=True, merged_content="мимо"),
            ]
        ),
    )

    await run_duplicate_merge(uuid4(), ctx)

    assert notes.inserted == []
    assert notes.superseded == []


@pytest.mark.asyncio
async def test_no_pairs_skip_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    """Нет пар выше порога → ноль LLM-вызовов."""
    ctx = make_ctx()
    calls = _stub_llm(monkeypatch, MergeOutput())

    await run_duplicate_merge(uuid4(), ctx)

    assert calls == []
