"""PostgreSQL repository для измерений — числовые ряды (core.measurements)."""

from datetime import datetime
from uuid import UUID

import asyncpg
from loguru import logger
import orjson

from bestfiend.memory.db import MemoryDatabaseClient
from bestfiend.memory.errors import MemoryPersistError
from bestfiend.memory.measurements.contracts import (
    MeasurementBucket,
    MeasurementDraft,
    MetricAggregate,
)


# Белый список выражений date_trunc: bucket приходит из Literal-контракта,
# но в SQL интерполируется только значение из этого словаря.
_BUCKET_TRUNC: dict[str, str] = {"day": "day", "week": "week", "month": "month"}


class MeasurementRepository:
    """Доступ к таблице measurements (одна строка = одна точка ряда)."""

    __slots__ = ("_db",)

    def __init__(self, db: MemoryDatabaseClient) -> None:
        self._db = db

    async def insert(self, user_id: UUID, draft: MeasurementDraft) -> tuple[int, bool]:
        """Вставляет точку ряда; возвращает (id, первая ли это запись метрики)."""
        try:
            # Проверка «новая ли метрика» вне транзакции: сигнал информационный
            # (подсказка модели в ответе тулзы), гонка с параллельной вставкой
            # безвредна.
            exists_row = await self._db.fetch_one(
                "SELECT EXISTS(SELECT 1 FROM measurements WHERE user_id = $1 AND metric = $2) AS known",
                user_id,
                draft.metric,
            )
            is_new = exists_row is None or not exists_row["known"]
            row = await self._db.fetch_one(
                """
                INSERT INTO measurements (user_id, metric, value, unit, tags, source, event_time)
                VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7)
                RETURNING id
                """,
                user_id,
                draft.metric,
                draft.value,
                draft.unit,
                orjson.dumps(draft.tags).decode(),
                draft.source,
                draft.event_time,
            )
        except asyncpg.PostgresError:
            logger.exception(
                "MeasurementRepository: insert failed user_id={} metric={}",
                user_id,
                draft.metric,
            )
            raise
        if row is None:
            raise MemoryPersistError("INSERT ... RETURNING id не вернул строку")
        return int(row["id"]), is_new

    async def aggregate(
        self,
        user_id: UUID,
        *,
        metric: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        bucket: MeasurementBucket | None = None,
    ) -> list[MetricAggregate]:
        """Агрегаты рядов за период: по метрикам, опционально по бакетам времени.

        Границы периода: event_time >= since и event_time < until.
        Без metric — сводка всех метрик пользователя.
        """
        conditions = ["user_id = $1"]
        args: list[object] = [user_id]
        if metric is not None:
            args.append(metric)
            conditions.append(f"metric = ${len(args)}")
        if since is not None:
            args.append(since)
            conditions.append(f"event_time >= ${len(args)}")
        if until is not None:
            args.append(until)
            conditions.append(f"event_time < ${len(args)}")

        bucket_select = ""
        bucket_group = ""
        if bucket is not None:
            trunc = _BUCKET_TRUNC[bucket]
            bucket_select = f", date_trunc('{trunc}', event_time) AS bucket_start"
            bucket_group = ", bucket_start"

        rows = await self._db.fetch(
            f"""
            SELECT
                metric,
                count(*) AS count,
                avg(value) AS value_avg,
                min(value) AS value_min,
                max(value) AS value_max,
                sum(value) AS value_sum,
                (array_agg(value ORDER BY event_time DESC) FILTER (WHERE value IS NOT NULL))[1] AS last_value,
                (array_agg(unit ORDER BY event_time DESC) FILTER (WHERE unit IS NOT NULL))[1] AS unit,
                min(event_time) AS first_at,
                max(event_time) AS last_at
                {bucket_select}
            FROM measurements
            WHERE {" AND ".join(conditions)}
            GROUP BY metric{bucket_group}
            ORDER BY metric{bucket_group}
            """,  # nosec B608 — SQL из внутренних констант, значения через $N-параметры
            *args,
        )
        return [_row_to_aggregate(row, bucketed=bucket is not None) for row in rows]


def _row_to_aggregate(row: asyncpg.Record, *, bucketed: bool) -> MetricAggregate:
    """asyncpg row → MetricAggregate."""
    return MetricAggregate(
        metric=row["metric"],
        count=int(row["count"]),
        value_avg=_as_float(row["value_avg"]),
        value_min=_as_float(row["value_min"]),
        value_max=_as_float(row["value_max"]),
        value_sum=_as_float(row["value_sum"]),
        last_value=_as_float(row["last_value"]),
        unit=row["unit"],
        first_at=row["first_at"],
        last_at=row["last_at"],
        bucket_start=row["bucket_start"] if bucketed else None,
    )


def _as_float(raw: object) -> float | None:
    """Числовая колонка агрегата → float (None остаётся None)."""
    return None if raw is None else float(raw)  # type: ignore[arg-type]
