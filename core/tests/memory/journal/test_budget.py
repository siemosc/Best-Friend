"""Пост-фаза журнала: Reflector при переполнении, FIFO-страховка, ops-след."""

from typing import Any
from uuid import uuid4

import pytest

from bestfiend.memory.observer.schemas import Observation, ObserverOutput
from bestfiend.memory.recall.render import render_note_line
from bestfiend.memory.settings import MemorySettings
from bestfiend.primitives.tokenizer import count_tokens
from tests.memory.fakes import (
    NoteRepositoryFake,
    OperationLogRepositoryFake,
    ReflectorFake,
    TransactionalDatabaseFake,
    TurnRepositoryFake,
    build_observer_service,
    make_journal_note,
    make_turn,
    stub_observer_llm,
)


_OUTPUT = ObserverOutput(
    observations=[Observation(content="новое наблюдение", weight="mid", subject="user")]
)


def _parts(
    journal_lines: int,
    *,
    budget_lines: int,
    reflector: ReflectorFake | None,
) -> dict[str, Any]:
    """Сервис с журналом из N одинаковых строк и бюджетом в M строк."""
    # Контент строк идентичен — токены равны, ассерты считают строки точно.
    journal = [make_journal_note("строка журнала") for _ in range(journal_lines)]
    line_tokens = count_tokens(render_note_line(journal[0]))
    notes = NoteRepositoryFake(journal=journal)
    ops = OperationLogRepositoryFake()
    db = TransactionalDatabaseFake()
    service = build_observer_service(
        turns=TurnRepositoryFake([make_turn(1), make_turn(2)]),
        notes=notes,
        ops=ops,
        db=db,
        settings=MemorySettings(
            observer_token_threshold=100,
            journal_token_budget=line_tokens * budget_lines,
        ),
        reflector=reflector,
    )
    return {
        "service": service,
        "notes": notes,
        "ops": ops,
        "db": db,
        "journal": journal,
    }


@pytest.mark.asyncio
async def test_journal_within_budget_skips_reflector(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Журнал в бюджете → Reflector не вызывается, вытеснений нет."""
    reflector = ReflectorFake(applied=True)
    parts = _parts(2, budget_lines=5, reflector=reflector)
    stub_observer_llm(monkeypatch, _OUTPUT)

    await parts["service"].maybe_run(uuid4())

    assert reflector.calls == []
    assert parts["notes"].evicted_ids == []


@pytest.mark.asyncio
async def test_reflector_success_avoids_fifo(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reflector ужал журнал в бюджет → FIFO не вытесняет ничего."""
    parts_holder: dict[str, Any] = {}

    def shrink_journal() -> None:
        notes: NoteRepositoryFake = parts_holder["notes"]
        notes.journal = notes.journal[:1]  # Reflector свернул журнал до одной строки

    reflector = ReflectorFake(applied=True, on_consolidate=shrink_journal)
    parts = _parts(4, budget_lines=2, reflector=reflector)
    parts_holder.update(parts)
    stub_observer_llm(monkeypatch, _OUTPUT)

    await parts["service"].maybe_run(uuid4())

    assert len(reflector.calls) == 1
    assert reflector.calls[0] == parts["journal"]  # на вход — переполненный журнал
    assert parts["notes"].evicted_ids == []  # FIFO не понадобился


@pytest.mark.asyncio
async def test_reflector_partial_then_fifo_tops_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reflector применился, но журнал всё ещё над бюджетом → FIFO добивает."""
    parts_holder: dict[str, Any] = {}

    def shrink_not_enough() -> None:
        notes: NoteRepositoryFake = parts_holder["notes"]
        notes.journal = notes.journal[:3]  # всё ещё больше бюджета в 2 строки

    reflector = ReflectorFake(applied=True, on_consolidate=shrink_not_enough)
    parts = _parts(5, budget_lines=2, reflector=reflector)
    parts_holder.update(parts)
    stub_observer_llm(monkeypatch, _OUTPUT)

    await parts["service"].maybe_run(uuid4())

    assert len(parts["notes"].evicted_ids) == 1  # 3 строки − 1 = бюджет 2
    evict_ops = parts["ops"].logged_ops("evict")
    assert [op.note_id for op in evict_ops] == parts["notes"].evicted_ids


@pytest.mark.asyncio
async def test_reflector_failure_falls_back_to_fifo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Сбой Reflector (False) → FIFO-вытеснение возвращает журнал в бюджет."""
    reflector = ReflectorFake(applied=False)
    parts = _parts(4, budget_lines=2, reflector=reflector)
    stub_observer_llm(monkeypatch, _OUTPUT)

    await parts["service"].maybe_run(uuid4())

    assert len(reflector.calls) == 1
    assert len(parts["notes"].evicted_ids) == 2  # 4 − 2 = бюджет
    # FIFO-вытеснение — отдельная короткая транзакция после персиста.
    evict_tx = parts["notes"].evict_executors[0]
    assert evict_tx is not None
    assert evict_tx.committed
