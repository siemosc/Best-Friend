"""Контракты слоя измерений."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal


MeasurementBucket = Literal["day", "week", "month"]


def normalize_metric_name(raw: str) -> str:
    """Каноничное имя метрики: нижний регистр, пробельные серии → одно подчёркивание."""
    return "_".join(raw.strip().lower().split())


@dataclass(frozen=True, slots=True)
class MeasurementDraft:
    """Точка ряда на запись: измерение (с value) или событие-счётчик (без)."""

    metric: str
    event_time: datetime
    value: float | None = None
    unit: str | None = None
    tags: dict[str, str] = field(default_factory=dict)
    source: str = "tool"


@dataclass(frozen=True, slots=True)
class MetricAggregate:
    """Агрегат метрики за период целиком или за один бакет (day/week/month)."""

    metric: str
    count: int
    value_avg: float | None
    value_min: float | None
    value_max: float | None
    value_sum: float | None
    # Последнее по event_time значение/единица — «какой у меня вес» = last_value.
    last_value: float | None
    unit: str | None
    first_at: datetime
    last_at: datetime
    bucket_start: datetime | None = None
