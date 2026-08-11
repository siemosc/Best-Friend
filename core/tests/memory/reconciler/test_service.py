"""Reconciler: батч-вызов, резолв решений, наследование pin, fail-open в ADD."""

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest

from bestfiend.memory.notes.contracts import NoteDraft
from bestfiend.memory.reconciler.schemas import ReconcileDecision, ReconcileOutput
from bestfiend.memory.reconciler.service import ReconcilerService
from bestfiend.memory.settings import MemorySettings
from tests.memory.fakes import NoteRepositoryFake, make_note


def _candidate(
    content: str, *, kind: str = "fact", embedding: list[float] | None = None
) -> NoteDraft:
    return NoteDraft(
        kind=kind,
        content=content,
        observed_at=datetime(2026, 6, 10, tzinfo=UTC),
        embedding=embedding,
    )


def _service(notes: NoteRepositoryFake | None = None) -> ReconcilerService:
    return ReconcilerService(
        notes_repository=notes or NoteRepositoryFake(),  # type: ignore[arg-type] — стаб по контракту
        settings=MemorySettings(reconciler_neighbors_k=5),
        llm_config={"provider": "openrouter", "model": "stub"},
    )


def stub_observer_llm(
    monkeypatch: pytest.MonkeyPatch, output: ReconcileOutput | None
) -> list[int]:
    """Подменяет LLM-вызов класса; возвращает счётчик вызовов."""
    calls: list[int] = []

    async def fake_invoke(*args: Any, **kwargs: Any) -> ReconcileOutput | None:
        calls.append(1)
        return output

    monkeypatch.setattr(
        "bestfiend.memory.reconciler.service.invoke_structured", fake_invoke
    )
    return calls


@pytest.mark.asyncio
async def test_empty_candidates_skip_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    """Пустой список кандидатов → ноль LLM-вызовов, пустой результат."""
    calls = stub_observer_llm(monkeypatch, ReconcileOutput())

    actions = await _service().reconcile(uuid4(), [])

    assert actions == []
    assert calls == []


@pytest.mark.asyncio
async def test_all_candidates_in_single_batch_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Все кандидаты (с соседями и без) идут одним батч-вызовом LLM."""
    neighbor = make_note("живёт в Москве", kind="fact")
    notes = NoteRepositoryFake(similar=[(neighbor, 0.8)])
    candidates = [
        _candidate("живёт в Белграде", embedding=[1.0, 0.0]),  # сосед найдётся
        _candidate("любит чай", kind="preference"),  # без embedding и тегов
        _candidate("пишет на Python", kind="fact"),
    ]
    calls = stub_observer_llm(monkeypatch, ReconcileOutput())

    actions = await _service(notes).reconcile(uuid4(), candidates)

    assert len(calls) == 1
    assert len(actions) == 3  # решений нет → все ADD (fail-open)
    assert all(a.action == "add" for a in actions)


@pytest.mark.asyncio
async def test_pin_available_for_candidate_without_neighbors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Кандидат без соседей получает pin-решение (не минует Reconciler)."""
    output = ReconcileOutput(
        decisions=[
            ReconcileDecision(
                candidate_index=0, action="add", pin=True, pin_section="preferences"
            )
        ]
    )
    stub_observer_llm(monkeypatch, output)

    actions = await _service().reconcile(
        uuid4(), [_candidate("любит краткость", kind="preference")]
    )

    assert actions[0].action == "add"
    assert actions[0].draft is not None
    assert actions[0].draft.pinned is True
    assert actions[0].draft.pin_section == "preferences"


@pytest.mark.asyncio
async def test_supersede_inherits_pin_from_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """supersede без явного pin наследует pinned/pin_section заменяемой."""
    pinned_target = make_note(
        "отвечает развёрнуто", kind="preference", pinned=True, pin_section="preferences"
    )
    notes = NoteRepositoryFake(similar=[(pinned_target, 0.9)])
    output = ReconcileOutput(
        decisions=[
            ReconcileDecision(candidate_index=0, action="supersede", target_index=0)
        ]
    )
    stub_observer_llm(monkeypatch, output)

    actions = await _service(notes).reconcile(
        uuid4(), [_candidate("отвечает кратко", kind="preference", embedding=[1.0])]
    )

    assert actions[0].action == "supersede"
    assert actions[0].target_note_id == pinned_target.id
    assert actions[0].draft is not None
    assert actions[0].draft.pinned is True  # унаследовано
    assert actions[0].draft.pin_section == "preferences"


@pytest.mark.asyncio
async def test_supersede_explicit_pin_overrides_inheritance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Явное pin-решение при supersede сильнее наследования."""
    plain_target = make_note("работает в X", kind="fact")
    notes = NoteRepositoryFake(similar=[(plain_target, 0.9)])
    output = ReconcileOutput(
        decisions=[
            ReconcileDecision(
                candidate_index=0,
                action="supersede",
                target_index=0,
                pin=True,
                pin_section="identity",
            )
        ]
    )
    stub_observer_llm(monkeypatch, output)

    actions = await _service(notes).reconcile(
        uuid4(), [_candidate("работает в Y", embedding=[1.0])]
    )

    assert actions[0].draft is not None
    assert actions[0].draft.pinned is True
    assert actions[0].draft.pin_section == "identity"


@pytest.mark.asyncio
async def test_contradict_marks_draft_status(monkeypatch: pytest.MonkeyPatch) -> None:
    """contradict: драфт получает status='contradicted', target указан."""
    target = make_note("не пьёт кофе", kind="fact")
    notes = NoteRepositoryFake(similar=[(target, 0.85)])
    output = ReconcileOutput(
        decisions=[
            ReconcileDecision(candidate_index=0, action="contradict", target_index=0)
        ]
    )
    stub_observer_llm(monkeypatch, output)

    actions = await _service(notes).reconcile(
        uuid4(), [_candidate("пьёт кофе каждое утро", embedding=[1.0])]
    )

    assert actions[0].action == "contradict"
    assert actions[0].draft is not None
    assert actions[0].draft.status == "contradicted"
    assert actions[0].target_note_id == target.id


@pytest.mark.asyncio
async def test_candidate_without_decision_defaults_to_add(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Кандидат, по которому LLM не дал решения, вставляется (fail-open)."""
    output = ReconcileOutput(
        decisions=[ReconcileDecision(candidate_index=0, action="noop")]
    )
    stub_observer_llm(monkeypatch, output)

    actions = await _service().reconcile(
        uuid4(), [_candidate("раз"), _candidate("два")]
    )

    assert actions[0].action == "noop"
    assert actions[1].action == "add"
    assert actions[1].draft is not None
    assert actions[1].draft.content == "два"


@pytest.mark.asyncio
async def test_invalid_target_index_falls_back_to_add(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """supersede с target_index вне диапазона соседей → ADD (исполнить нечего)."""
    output = ReconcileOutput(
        decisions=[
            ReconcileDecision(candidate_index=0, action="supersede", target_index=7)
        ]
    )
    stub_observer_llm(monkeypatch, output)

    actions = await _service().reconcile(uuid4(), [_candidate("знание")])

    assert actions[0].action == "add"
    assert actions[0].draft is not None


@pytest.mark.asyncio
async def test_noop_with_invalid_target_falls_back_to_add(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """noop с явным, но невалидным target_index → ADD (малоформированное решение)."""
    output = ReconcileOutput(
        decisions=[ReconcileDecision(candidate_index=0, action="noop", target_index=5)]
    )
    stub_observer_llm(monkeypatch, output)

    actions = await _service().reconcile(uuid4(), [_candidate("знание")])

    assert actions[0].action == "add"
    assert actions[0].draft is not None
    assert actions[0].draft.content == "знание"


@pytest.mark.asyncio
async def test_noop_without_target_is_valid(monkeypatch: pytest.MonkeyPatch) -> None:
    """noop без target_index — валидное решение (дубль без указания соседа)."""
    output = ReconcileOutput(
        decisions=[ReconcileDecision(candidate_index=0, action="noop")]
    )
    stub_observer_llm(monkeypatch, output)

    actions = await _service().reconcile(uuid4(), [_candidate("знание")])

    assert actions[0].action == "noop"
    assert actions[0].draft is None


@pytest.mark.asyncio
async def test_llm_failure_degrades_to_add_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Сбой LLM (None) → все кандидаты ADD как есть (V1-поведение)."""
    stub_observer_llm(monkeypatch, None)
    candidates = [_candidate("раз"), _candidate("два", kind="preference")]

    actions = await _service().reconcile(uuid4(), candidates)

    assert [a.action for a in actions] == ["add", "add"]
    assert [a.draft for a in actions] == candidates


@pytest.mark.asyncio
async def test_neighbors_search_uses_candidate_kind(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Соседи ищутся по kind кандидата: vector + entity-ветки, без дублей."""
    shared = make_note("общая заметка", kind="fact")
    entity_only = make_note("тегированная", kind="fact")
    notes = NoteRepositoryFake(
        similar=[(shared, 0.8)], by_entities=[shared, entity_only]
    )
    entity_id = uuid4()
    candidate = NoteDraft(
        kind="fact",
        content="кандидат",
        observed_at=datetime(2026, 6, 10, tzinfo=UTC),
        embedding=[1.0, 0.0],
        entity_ids=(entity_id,),
    )
    captured: dict[str, Any] = {}

    async def fake_invoke(
        llm_config: Any, schema: Any, messages: Any, **kwargs: Any
    ) -> ReconcileOutput:
        captured["prompt"] = messages[1].content
        return ReconcileOutput()

    monkeypatch.setattr(
        "bestfiend.memory.reconciler.service.invoke_structured", fake_invoke
    )

    await _service(notes).reconcile(uuid4(), [candidate])

    assert notes.find_similar_calls[0][1] == ["fact"]  # kinds
    assert notes.find_by_entities_calls[0][0] == [entity_id]
    # Сосед из двух веток показан один раз, entity-сосед добавлен следом.
    assert captured["prompt"].count("общая заметка") == 1
    assert "тегированная" in captured["prompt"]
