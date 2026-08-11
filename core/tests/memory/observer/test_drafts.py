"""Observer: резолв сущностей в драфтах и вытеснение из журнала по бюджету."""

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest

from bestfiend.memory.observer.schemas import (
    FactCandidate,
    Observation,
    ObserverOutput,
)
from bestfiend.memory.observer.service import ObserverService
from bestfiend.memory.recall.render import render_note_line
from bestfiend.memory.settings import MemorySettings
from bestfiend.primitives.tokenizer import count_tokens
from tests.memory.fakes import (
    EntityRepositoryFake,
    NoteRepositoryFake,
    TurnRepositoryFake,
    build_observer_service,
    make_journal_note,
    make_turn,
    stub_observer_llm,
)


@pytest.mark.asyncio
async def test_entities_resolved_and_created(
    observer_parts: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Известное имя → существующий id; новое упомянутое — создаётся; пустой тег пропущен."""
    known_id = uuid4()
    entities: EntityRepositoryFake = observer_parts["entities"]
    entities.known = {"BestFiend": known_id}
    service: ObserverService = observer_parts["service"]
    output = ObserverOutput(
        observations=[
            Observation(
                content="обсуждали проект",
                weight="mid",
                subject="user",
                entities=["BestFiend", "SeaweedFS"],
            )
        ],
        candidates=[
            FactCandidate(
                content="хранилище — SeaweedFS",
                kind="fact",
                subject="world",
                entities=["SeaweedFS"],
            )
        ],
        new_entities=["SeaweedFS", "НеУпомянутая"],
    )
    stub_observer_llm(monkeypatch, output)

    await service.maybe_run(uuid4())

    notes: NoteRepositoryFake = observer_parts["notes"]
    assert entities.created == ["SeaweedFS"]  # только реально упомянутая в тегах
    obs_draft = notes.inserted[0]
    assert known_id in obs_draft.entity_ids
    assert entities.known["SeaweedFS"] in obs_draft.entity_ids
    assert obs_draft.subject == "user"  # модельный субъект доезжает до драфта
    cand_draft = notes.inserted[1]
    assert cand_draft.kind == "fact"
    assert cand_draft.subject == "world"
    assert cand_draft.in_journal is False
    assert cand_draft.entity_ids == (entities.known["SeaweedFS"],)


@pytest.mark.asyncio
async def test_mixed_case_tags_resolve_to_one_entity(
    observer_parts: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Смешанный регистр тега в батче резолвится в одну сущность (casefold)."""
    known_id = uuid4()
    entities: EntityRepositoryFake = observer_parts["entities"]
    entities.known = {"BestFiend": known_id}
    service: ObserverService = observer_parts["service"]
    output = ObserverOutput(
        observations=[
            Observation(
                content="раз", weight="mid", subject="user", entities=["BestFiend"]
            ),
            Observation(
                content="два", weight="mid", subject="user", entities=["bestfiend"]
            ),
        ],
    )
    stub_observer_llm(monkeypatch, output)

    await service.maybe_run(uuid4())

    notes: NoteRepositoryFake = observer_parts["notes"]
    assert entities.created == []  # дубль-сущность не создана
    assert [d.entity_ids for d in notes.inserted] == [(known_id,), (known_id,)]


@pytest.mark.asyncio
async def test_journal_eviction_order_weight_then_age(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Переполнение бюджета журнала → вытеснение (weight asc, observed_at asc); high уходит последним."""
    old = datetime(2026, 6, 1, tzinfo=UTC)
    new = datetime(2026, 6, 9, tzinfo=UTC)
    # Контент одинаковой длины — токены равны, порядок решают weight и возраст.
    low_old = make_journal_note("заметка номер один", weight=0, observed_at=old)
    low_new = make_journal_note("заметка номер два!", weight=0, observed_at=new)
    high_old = make_journal_note("заметка номер три!", weight=2, observed_at=old)
    notes = NoteRepositoryFake(journal=[low_old, low_new, high_old])
    # Бюджет вмещает ровно одну строку журнала — две другие должны вытесниться.
    one_line_budget = count_tokens(render_note_line(high_old))
    service = build_observer_service(
        turns=TurnRepositoryFake([make_turn(1), make_turn(2)]),
        notes=notes,
        settings=MemorySettings(
            observer_token_threshold=100, journal_token_budget=one_line_budget
        ),
    )
    output = ObserverOutput(
        observations=[
            Observation(content="новое наблюдение", weight="mid", subject="user")
        ]
    )
    stub_observer_llm(monkeypatch, output)

    await service.maybe_run(uuid4())

    assert notes.evicted_ids[0] == low_old.id  # самый лёгкий и старый — первым
    assert notes.evicted_ids[1] == low_new.id
    assert high_old.id not in notes.evicted_ids


@pytest.mark.asyncio
async def test_empty_output_advances_watermark(
    observer_parts: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Пустой выход LLM (болтовня) — заметок нет, но watermark двигается."""
    service: ObserverService = observer_parts["service"]
    stub_observer_llm(monkeypatch, ObserverOutput())

    await service.maybe_run(uuid4())

    assert observer_parts["notes"].inserted == []
    assert observer_parts["watermarks"].positions["observer"] == 2
