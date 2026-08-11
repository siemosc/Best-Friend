"""Тулзы измерений: схемы, канонизация, время, маппинг аргументов в репозиторий."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from bestfiend.memory.agent_tools import (
    MEMORY_STATS_NAME,
    MEMORY_TOOL_NAMES,
    MEMORY_TRACK_NAME,
    build_memory_tools,
)
from tests.memory.fakes import (
    MeasurementRepositoryFake,
    make_agent_tools_runtime,
    make_metric_aggregate,
)


def test_track_and_stats_schemas_expose_contract_fields() -> None:
    """Обе тулзы в top-level наборе; схемы несут контрактные поля."""
    tools = build_memory_tools(make_agent_tools_runtime(), uuid4())
    assert MEMORY_TRACK_NAME in MEMORY_TOOL_NAMES
    assert MEMORY_STATS_NAME in MEMORY_TOOL_NAMES

    track_schema = tools[MEMORY_TRACK_NAME].args_schema
    stats_schema = tools[MEMORY_STATS_NAME].args_schema
    assert track_schema is not None and stats_schema is not None
    assert set(track_schema.model_fields) == {  # type: ignore[union-attr]
        "metric",
        "value",
        "unit",
        "event_time",
        "tags",
    }
    assert set(stats_schema.model_fields) == {  # type: ignore[union-attr]
        "metric",
        "from_date",
        "to_date",
        "group_by",
    }


@pytest.mark.asyncio
async def test_track_canonizes_metric_and_normalizes_naive_time() -> None:
    """Имя метрики канонизируется, наивное event_time становится aware UTC."""
    measurements = MeasurementRepositoryFake()
    tools = build_memory_tools(
        make_agent_tools_runtime(measurements=measurements), uuid4()
    )

    result = await tools[MEMORY_TRACK_NAME].coroutine(  # type: ignore[misc]
        metric=" Вес Тела ",
        value=70.2,
        unit="kg",
        event_time=datetime(2026, 7, 1, 8, 30),  # naive от модели
    )

    [(_, draft)] = measurements.inserted
    assert draft.metric == "вес_тела"
    assert draft.event_time == datetime(2026, 7, 1, 8, 30, tzinfo=UTC)
    assert draft.value == 70.2
    assert draft.source == "tool"
    assert "вес_тела" in result
    assert "новая метрика" in result  # первая запись метрики


@pytest.mark.asyncio
async def test_track_event_without_value_defaults_to_now() -> None:
    """Событие без value: время — сейчас (UTC), повтор метрики без пометки «новая»."""
    measurements = MeasurementRepositoryFake()
    tools = build_memory_tools(
        make_agent_tools_runtime(measurements=measurements), uuid4()
    )
    before = datetime.now(UTC)

    first = await tools[MEMORY_TRACK_NAME].coroutine(metric="gym")  # type: ignore[misc]
    second = await tools[MEMORY_TRACK_NAME].coroutine(metric="gym")  # type: ignore[misc]

    [(_, draft_first), (_, draft_second)] = measurements.inserted
    assert draft_first.value is None
    assert before <= draft_first.event_time <= datetime.now(UTC)
    assert draft_second.tags == {}
    assert "новая метрика" in first
    assert "новая метрика" not in second


@pytest.mark.asyncio
async def test_track_rejects_blank_metric() -> None:
    """Пробельное имя метрики → отказ без записи."""
    measurements = MeasurementRepositoryFake()
    tools = build_memory_tools(
        make_agent_tools_runtime(measurements=measurements), uuid4()
    )

    result = await tools[MEMORY_TRACK_NAME].coroutine(metric="   ")  # type: ignore[misc]

    assert measurements.inserted == []
    assert "пустое имя" in result


@pytest.mark.asyncio
async def test_stats_maps_arguments_and_extends_midnight_to_date() -> None:
    """metric канонизируется, полуночный to_date растягивается на весь день."""
    measurements = MeasurementRepositoryFake(
        [make_metric_aggregate("вес", last_value=70.0)]
    )
    tools = build_memory_tools(
        make_agent_tools_runtime(measurements=measurements), uuid4()
    )

    result = await tools[MEMORY_STATS_NAME].coroutine(  # type: ignore[misc]
        metric="Вес",
        from_date=datetime(2026, 6, 1, tzinfo=UTC),
        to_date=datetime(2026, 6, 30, tzinfo=UTC),
        group_by="week",
    )

    [call] = measurements.aggregate_calls
    assert call["metric"] == "вес"
    assert call["since"] == datetime(2026, 6, 1, tzinfo=UTC)
    # to_date «включительно»: полуночная граница сдвинута на сутки вперёд.
    assert call["until"] == datetime(2026, 6, 30, tzinfo=UTC) + timedelta(days=1)
    assert call["bucket"] == "week"
    assert result.startswith("Статистика измерений:")


@pytest.mark.asyncio
async def test_stats_keeps_explicit_time_boundary() -> None:
    """to_date с ненулевым временем — точная граница, без сдвига."""
    measurements = MeasurementRepositoryFake(
        [make_metric_aggregate("вес", last_value=70.0)]
    )
    tools = build_memory_tools(
        make_agent_tools_runtime(measurements=measurements), uuid4()
    )
    boundary = datetime(2026, 6, 30, 15, 30, tzinfo=UTC)

    await tools[MEMORY_STATS_NAME].coroutine(metric="вес", to_date=boundary)  # type: ignore[misc]

    [call] = measurements.aggregate_calls
    assert call["until"] == boundary


@pytest.mark.asyncio
async def test_stats_empty_answers_differ_for_metric_and_overview() -> None:
    """Пусто с метрикой → подсказка про список; пусто без метрики → «не велись»."""
    tools = build_memory_tools(
        make_agent_tools_runtime(measurements=MeasurementRepositoryFake()), uuid4()
    )

    by_metric = await tools[MEMORY_STATS_NAME].coroutine(metric="вес")  # type: ignore[misc]
    overview = await tools[MEMORY_STATS_NAME].coroutine()  # type: ignore[misc]

    assert "вес" in by_metric and "без аргументов" in by_metric
    assert "не велись" in overview
