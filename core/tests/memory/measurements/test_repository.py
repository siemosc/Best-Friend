"""Тесты MeasurementRepository: insert (jsonb, is_new) и сборка aggregate-запроса."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import orjson
import pytest

from bestfiend.memory.measurements.contracts import MeasurementDraft
from bestfiend.memory.measurements.repository import MeasurementRepository


_EVENT_TIME = datetime(2026, 7, 1, 8, 30, tzinfo=UTC)


def _aggregate_row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "metric": "вес",
        "count": 2,
        "value_avg": 70.0,
        "value_min": 69.8,
        "value_max": 70.2,
        "value_sum": 140.0,
        "last_value": 69.8,
        "unit": "kg",
        "first_at": _EVENT_TIME,
        "last_at": _EVENT_TIME,
    }
    row.update(overrides)
    return row


@pytest.mark.asyncio
async def test_insert_serializes_tags_and_reports_new_metric() -> None:
    """INSERT: tags уходят jsonb-строкой, порядок args; первая запись → is_new."""
    db = AsyncMock()
    db.fetch_one.side_effect = [{"known": False}, {"id": 7}]
    repo = MeasurementRepository(db)
    user_id = uuid4()
    draft = MeasurementDraft(
        metric="вес",
        event_time=_EVENT_TIME,
        value=70.2,
        unit="kg",
        tags={"note": "утром"},
    )

    measurement_id, is_new = await repo.insert(user_id, draft)

    assert measurement_id == 7
    assert is_new is True
    sql, *args = db.fetch_one.call_args.args
    assert "INSERT INTO measurements" in sql
    assert "$5::jsonb" in sql
    assert args[0] == user_id
    assert args[1] == "вес"
    assert args[2] == 70.2
    assert args[3] == "kg"
    assert orjson.loads(args[4]) == {"note": "утром"}
    assert args[5] == "tool"
    assert args[6] == _EVENT_TIME


@pytest.mark.asyncio
async def test_insert_known_metric_is_not_new() -> None:
    """Метрика уже встречалась → is_new False."""
    db = AsyncMock()
    db.fetch_one.side_effect = [{"known": True}, {"id": 8}]
    repo = MeasurementRepository(db)

    _, is_new = await repo.insert(uuid4(), MeasurementDraft("gym", _EVENT_TIME))

    assert is_new is False


@pytest.mark.asyncio
async def test_aggregate_without_filters_groups_by_metric() -> None:
    """Без фильтров: WHERE только user_id, группировка по метрике, без бакета."""
    db = AsyncMock()
    db.fetch.return_value = [_aggregate_row()]
    repo = MeasurementRepository(db)
    user_id = uuid4()

    [aggregate] = await repo.aggregate(user_id)

    sql, *args = db.fetch.call_args.args
    assert args == [user_id]
    assert "WHERE user_id = $1" in sql
    assert "date_trunc" not in sql
    assert "GROUP BY metric\n" in sql
    assert aggregate.metric == "вес"
    assert aggregate.count == 2
    assert aggregate.last_value == 69.8
    assert aggregate.bucket_start is None


@pytest.mark.asyncio
async def test_aggregate_full_filters_number_params_in_order() -> None:
    """metric + since + until нумеруются $2/$3/$4 и уезжают в args в том же порядке."""
    db = AsyncMock()
    db.fetch.return_value = []
    repo = MeasurementRepository(db)
    user_id = uuid4()
    since = datetime(2026, 6, 1, tzinfo=UTC)
    until = datetime(2026, 7, 1, tzinfo=UTC)

    await repo.aggregate(user_id, metric="вес", since=since, until=until)

    sql, *args = db.fetch.call_args.args
    assert args == [user_id, "вес", since, until]
    assert "metric = $2" in sql
    assert "event_time >= $3" in sql
    assert "event_time < $4" in sql


@pytest.mark.asyncio
async def test_aggregate_bucket_adds_date_trunc_and_maps_bucket_start() -> None:
    """bucket=week: date_trunc в SELECT/GROUP BY, bucket_start мапится в агрегат."""
    week_start = datetime(2026, 6, 29, tzinfo=UTC)
    db = AsyncMock()
    db.fetch.return_value = [_aggregate_row(bucket_start=week_start)]
    repo = MeasurementRepository(db)

    [aggregate] = await repo.aggregate(uuid4(), bucket="week")

    sql = db.fetch.call_args.args[0]
    assert "date_trunc('week', event_time) AS bucket_start" in sql
    assert "GROUP BY metric, bucket_start" in sql
    assert aggregate.bucket_start == week_start
