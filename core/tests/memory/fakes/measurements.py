"""Тестовые двойники измерений памяти."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from bestfiend.memory.measurements.contracts import (
    MeasurementBucket,
    MeasurementDraft,
    MetricAggregate,
)


def make_metric_aggregate(
    metric: str,
    *,
    count: int = 1,
    last_value: float | None = None,
    unit: str | None = None,
    first_at: datetime | None = None,
    last_at: datetime | None = None,
    bucket_start: datetime | None = None,
) -> MetricAggregate:
    """Создаёт агрегат метрики с тестовыми значениями по умолчанию."""
    moment = datetime(2026, 6, 3, 12, 0, tzinfo=UTC)
    return MetricAggregate(
        metric=metric,
        count=count,
        value_avg=last_value,
        value_min=last_value,
        value_max=last_value,
        value_sum=last_value,
        last_value=last_value,
        unit=unit,
        first_at=first_at or moment,
        last_at=last_at or moment,
        bucket_start=bucket_start,
    )


class MeasurementRepositoryFake:
    """Репозиторий с журналом вставок и настраиваемыми агрегатами."""

    def __init__(self, aggregates: list[MetricAggregate] | None = None) -> None:
        self.aggregates = aggregates or []
        self.inserted: list[tuple[UUID, MeasurementDraft]] = []
        self.known_metrics: set[str] = set()
        self.aggregate_calls: list[dict[str, Any]] = []

    async def insert(self, user_id: UUID, draft: MeasurementDraft) -> tuple[int, bool]:
        self.inserted.append((user_id, draft))
        is_new = draft.metric not in self.known_metrics
        self.known_metrics.add(draft.metric)
        return len(self.inserted), is_new

    async def aggregate(
        self,
        user_id: UUID,
        *,
        metric: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        bucket: MeasurementBucket | None = None,
    ) -> list[MetricAggregate]:
        self.aggregate_calls.append(
            {"metric": metric, "since": since, "until": until, "bucket": bucket}
        )
        if metric is None:
            return list(self.aggregates)
        return [
            aggregate for aggregate in self.aggregates if aggregate.metric == metric
        ]
