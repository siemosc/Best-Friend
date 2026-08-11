"""Observer._persist_run: исполнение решений Reconciler'а, промоушен, ops-лог.

Все шаги — один executor одной транзакции; LLM-вызовы уже сделаны до её открытия.
"""

import dataclasses
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest

from bestfiend.memory.notes.contracts import NoteDraft
from bestfiend.memory.observer.schemas import FactCandidate, ObserverOutput
from bestfiend.memory.observer.service import ObserverService
from bestfiend.memory.reconciler.service import ReconciledAction
from bestfiend.memory.settings import MemorySettings
from bestfiend.primitives.tokenizer import count_tokens
from tests.memory.fakes import (
    NoteRepositoryFake,
    OperationLogRepositoryFake,
    ReconcilerFake,
    TransactionalDatabaseFake,
    TurnRepositoryFake,
    build_observer_service,
    make_note,
    make_turn,
    stub_observer_llm,
)


_TWO_CANDIDATES = ObserverOutput(
    candidates=[
        FactCandidate(content="живёт в Белграде", kind="fact", subject="user"),
        FactCandidate(
            content="любит краткие ответы", kind="preference", subject="user"
        ),
    ]
)


def _service_with(
    decide: Any,
    *,
    notes: NoteRepositoryFake | None = None,
    ops: OperationLogRepositoryFake | None = None,
    db: TransactionalDatabaseFake | None = None,
    settings: MemorySettings | None = None,
) -> tuple[
    ObserverService,
    NoteRepositoryFake,
    OperationLogRepositoryFake,
    TransactionalDatabaseFake,
    ReconcilerFake,
]:
    notes = notes or NoteRepositoryFake()
    ops = ops or OperationLogRepositoryFake()
    db = db or TransactionalDatabaseFake()
    reconciler = ReconcilerFake(decide)
    service = build_observer_service(
        turns=TurnRepositoryFake([make_turn(1), make_turn(2)]),
        notes=notes,
        ops=ops,
        db=db,
        settings=settings,
        reconciler=reconciler,
    )
    return service, notes, ops, db, reconciler


@pytest.mark.asyncio
async def test_supersede_inserts_new_and_marks_old(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """supersede: новая вставлена, старая помечена тем же executor; ops с парой id."""
    old_note = make_note("живёт в Москве", kind="fact")

    def decide(candidates: list[NoteDraft]) -> list[ReconciledAction]:
        return [
            ReconciledAction(
                action="supersede", draft=candidates[0], target_note_id=old_note.id
            ),
            ReconciledAction(action="add", draft=candidates[1]),
        ]

    service, notes, ops, db, _ = _service_with(decide)
    stub_observer_llm(monkeypatch, _TWO_CANDIDATES)

    await service.maybe_run(uuid4())

    tx = db.transactions[0]
    assert tx.committed
    assert notes.superseded == [(old_note.id, notes.inserted_with_ids[0][1])]
    assert notes.supersede_executors == [tx]
    supersede_ops = ops.logged_ops("supersede")
    assert len(supersede_ops) == 1
    assert supersede_ops[0].target_note_id == old_note.id
    assert supersede_ops[0].note_id == notes.inserted_with_ids[0][1]
    assert len(ops.logged_ops("add")) == 1  # второй кандидат


@pytest.mark.asyncio
async def test_contradict_inserts_marked_draft_and_flags_old(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """contradict: новая вставлена со status='contradicted', старая помечена; ops."""
    old_note = make_note("работает в офисе", kind="fact")

    def decide(candidates: list[NoteDraft]) -> list[ReconciledAction]:
        contradicted = dataclasses.replace(candidates[0], status="contradicted")
        return [
            ReconciledAction(
                action="contradict", draft=contradicted, target_note_id=old_note.id
            ),
            ReconciledAction(action="noop", draft=None),
        ]

    service, notes, ops, db, _ = _service_with(decide)
    stub_observer_llm(monkeypatch, _TWO_CANDIDATES)

    await service.maybe_run(uuid4())

    inserted = notes.inserted
    assert len(inserted) == 1  # noop-кандидат не вставлен
    assert inserted[0].status == "contradicted"
    assert notes.contradicted_ids == [old_note.id]
    assert notes.contradict_executors == [db.transactions[0]]
    assert len(ops.logged_ops("contradict")) == 1
    assert len(ops.logged_ops("noop")) == 1


@pytest.mark.asyncio
async def test_noop_candidate_not_inserted_but_leaves_trace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """noop: кандидат пропущен, ops-запись с найденным дублем и фрагментом контента."""
    duplicate = make_note("живёт в Белграде", kind="fact")

    def decide(candidates: list[NoteDraft]) -> list[ReconciledAction]:
        return [
            ReconciledAction(
                action="noop", draft=candidates[0], target_note_id=duplicate.id
            ),
            ReconciledAction(action="add", draft=candidates[1]),
        ]

    service, notes, ops, _, _ = _service_with(decide)
    stub_observer_llm(monkeypatch, _TWO_CANDIDATES)

    await service.maybe_run(uuid4())

    assert len(notes.inserted) == 1
    noop_ops = ops.logged_ops("noop")
    assert noop_ops[0].target_note_id == duplicate.id
    assert "Белграде" in (noop_ops[0].detail or "")


@pytest.mark.asyncio
async def test_pin_decision_inserts_pinned_and_logs_detail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """add+pin: pinned-драфт вставлен, ops add несёт секцию профиля."""

    def decide(candidates: list[NoteDraft]) -> list[ReconciledAction]:
        pinned = dataclasses.replace(
            candidates[1], pinned=True, pin_section="preferences"
        )
        return [
            ReconciledAction(action="add", draft=candidates[0]),
            ReconciledAction(action="add", draft=pinned),
        ]

    service, notes, ops, _, _ = _service_with(decide)
    stub_observer_llm(monkeypatch, _TWO_CANDIDATES)

    await service.maybe_run(uuid4())

    pinned_drafts = [d for d in notes.inserted if d.pinned]
    assert len(pinned_drafts) == 1
    assert pinned_drafts[0].pin_section == "preferences"
    add_details = [op.detail for op in ops.logged_ops("add")]
    assert "pin=preferences" in add_details


@pytest.mark.asyncio
async def test_profile_overflow_demotes_least_used_in_same_tx(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Переполнение секции после pin → демоция наименее используемой, ops demote, та же tx."""
    observed_old = datetime(2026, 5, 1, tzinfo=UTC)
    low_use = make_note(
        "правило раз",
        kind="rule",
        pinned=True,
        pin_section="preferences",
        use_count=0,
        observed_at=observed_old,
    )
    high_use = make_note(
        "правило два",
        kind="rule",
        pinned=True,
        pin_section="preferences",
        use_count=7,
    )
    new_content = "любит краткие ответы"
    budget = count_tokens(new_content) + count_tokens(high_use.content)

    def decide(candidates: list[NoteDraft]) -> list[ReconciledAction]:
        pinned = dataclasses.replace(
            candidates[1], pinned=True, pin_section="preferences"
        )
        return [
            ReconciledAction(action="noop", draft=None),
            ReconciledAction(action="add", draft=pinned),
        ]

    service, notes, ops, db, _ = _service_with(
        decide,
        notes=NoteRepositoryFake(pinned=[low_use, high_use]),
        settings=MemorySettings(
            observer_token_threshold=100,
            journal_token_budget=10_000,
            profile_section_token_budget=budget,
        ),
    )
    stub_observer_llm(monkeypatch, _TWO_CANDIDATES)

    await service.maybe_run(uuid4())

    assert notes.demoted_ids == [low_use.id]  # наименее используемая ушла
    assert notes.demote_executors == [db.transactions[0]]
    demote_ops = ops.logged_ops("demote")
    assert [op.note_id for op in demote_ops] == [low_use.id]
