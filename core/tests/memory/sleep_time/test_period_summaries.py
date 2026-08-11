"""Сводки недель: идемпотентность, минимум наблюдений, event_time=понедельник, cap."""

import dataclasses
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest

from bestfiend.memory.settings import MemorySettings
from bestfiend.memory.sleep_time.period_summaries import run_period_summaries
from bestfiend.memory.sleep_time.period_summaries import service as period_summaries
from bestfiend.memory.sleep_time.period_summaries.schemas import PeriodSummaryOutput
from tests.memory.fakes import (
    MeasurementRepositoryFake,
    NoteRepositoryFake,
    OperationLogRepositoryFake,
    TransactionalDatabaseFake,
    make_metric_aggregate,
    make_note,
)

from .conftest import make_ctx


# Фиксированное «сейчас»: среда 2026-06-10; последняя закрытая неделя — с 2026-06-01.
_NOW = datetime(2026, 6, 10, 14, 0, tzinfo=UTC)
_LAST_CLOSED_MONDAY = datetime(2026, 6, 1, tzinfo=UTC)


def _observations(week_start: datetime, count: int) -> list[Any]:
    return [
        dataclasses.replace(
            make_note(f"наблюдение {i}", kind="observation"),
            observed_at=week_start + timedelta(days=i % 7, hours=10),
            source_turn_start=i + 1,
            source_turn_end=i + 2,
        )
        for i in range(count)
    ]


def _stub_llm(
    monkeypatch: pytest.MonkeyPatch, output: PeriodSummaryOutput | None
) -> list[Any]:
    calls: list[Any] = []

    async def fake_invoke(ctx: Any, schema: Any, messages: Any, **kwargs: Any) -> Any:
        calls.append(messages)
        return output

    monkeypatch.setattr(period_summaries, "invoke_structured", fake_invoke)
    return calls


@pytest.mark.asyncio
async def test_closed_week_summarized_with_monday_event_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Закрытая неделя с достаточными наблюдениями → сводка с event_time=понедельник."""
    notes = NoteRepositoryFake()
    notes.observations = _observations(_LAST_CLOSED_MONDAY, 6)
    ops = OperationLogRepositoryFake()
    db = TransactionalDatabaseFake()
    ctx = make_ctx(notes=notes, ops=ops, db=db)
    _stub_llm(monkeypatch, PeriodSummaryOutput(content="Итоги недели: ..."))

    await run_period_summaries(uuid4(), ctx, now=_NOW)

    [(draft, summary_id)] = notes.inserted_with_ids
    assert draft.kind == "period_summary"
    assert draft.event_time == _LAST_CLOSED_MONDAY
    assert draft.source_turn_start == 1  # min span наблюдений
    assert draft.source_turn_end == 7  # max span наблюдений
    add_ops = ops.logged_ops("add")
    assert add_ops[0].note_id == summary_id
    assert db.transactions[0].committed


@pytest.mark.asyncio
async def test_existing_summary_skips_week(monkeypatch: pytest.MonkeyPatch) -> None:
    """Сводка недели уже есть → неделя скипается без LLM-вызова по ней."""
    notes = NoteRepositoryFake()
    notes.observations = _observations(_LAST_CLOSED_MONDAY, 6)
    notes.existing_summaries = {
        _LAST_CLOSED_MONDAY: make_note("готовая сводка", kind="period_summary")
    }
    ctx = make_ctx(notes=notes)
    calls = _stub_llm(monkeypatch, PeriodSummaryOutput(content="x"))

    await run_period_summaries(uuid4(), ctx, now=_NOW)

    assert calls == []  # старые недели без наблюдений тоже не дали вызовов
    assert notes.inserted == []


@pytest.mark.asyncio
async def test_too_few_observations_skips_week(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Меньше минимума наблюдений → сводки нет."""
    notes = NoteRepositoryFake()
    notes.observations = _observations(_LAST_CLOSED_MONDAY, 2)  # < min 5
    ctx = make_ctx(notes=notes)
    calls = _stub_llm(monkeypatch, PeriodSummaryOutput(content="x"))

    await run_period_summaries(uuid4(), ctx, now=_NOW)

    assert calls == []
    assert notes.inserted == []


@pytest.mark.asyncio
async def test_measurements_only_week_generates_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Неделя без наблюдений, но с измерениями → сводка с дайджестом, span пустой."""
    notes = NoteRepositoryFake()
    measurements = MeasurementRepositoryFake(
        [make_metric_aggregate("вес", last_value=70.2, unit="kg")]
    )
    settings = MemorySettings(sleep_max_summaries_per_cycle=1)
    ctx = make_ctx(notes=notes, measurements=measurements, settings=settings)
    calls = _stub_llm(monkeypatch, PeriodSummaryOutput(content="Неделя измерений"))

    await run_period_summaries(uuid4(), ctx, now=_NOW)

    [draft] = notes.inserted
    assert draft.kind == "period_summary"
    assert draft.source_turn_start is None  # наблюдений не было — span пуст
    human = calls[0][1].content
    assert "Измерения недели (агрегаты):" in human
    assert "вес" in human
    assert "Наблюдения недели" not in human


@pytest.mark.asyncio
async def test_digest_accompanies_observations_in_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Неделя с наблюдениями и измерениями → в промпте оба блока."""
    notes = NoteRepositoryFake()
    notes.observations = _observations(_LAST_CLOSED_MONDAY, 6)
    measurements = MeasurementRepositoryFake([make_metric_aggregate("gym", count=3)])
    settings = MemorySettings(sleep_max_summaries_per_cycle=1)
    ctx = make_ctx(notes=notes, measurements=measurements, settings=settings)
    calls = _stub_llm(monkeypatch, PeriodSummaryOutput(content="Итоги"))

    await run_period_summaries(uuid4(), ctx, now=_NOW)

    human = calls[0][1].content
    assert "Наблюдения недели:" in human
    assert "Измерения недели (агрегаты):" in human
    assert "gym" in human


@pytest.mark.asyncio
async def test_cap_one_summary_per_cycle(monkeypatch: pytest.MonkeyPatch) -> None:
    """Две недели без сводок → за цикл закрывается одна (свежая)."""
    previous_monday = _LAST_CLOSED_MONDAY - timedelta(weeks=1)
    notes = NoteRepositoryFake()
    notes.observations = [
        *_observations(_LAST_CLOSED_MONDAY, 5),
        *_observations(previous_monday, 5),
    ]
    settings = MemorySettings(sleep_max_summaries_per_cycle=1)
    ctx = make_ctx(notes=notes, settings=settings)
    calls = _stub_llm(monkeypatch, PeriodSummaryOutput(content="Итоги"))

    await run_period_summaries(uuid4(), ctx, now=_NOW)

    assert len(calls) == 1
    [draft] = notes.inserted
    assert draft.event_time == _LAST_CLOSED_MONDAY  # свежая неделя приоритетнее
