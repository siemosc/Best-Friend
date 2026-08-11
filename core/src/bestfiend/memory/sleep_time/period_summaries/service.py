"""Сводки периодов: закрытая ISO-неделя наблюдений и измерений → одна плотная запись.

Сводка — заметка kind='period_summary' с event_time = понедельник недели (UTC):
идемпотентность — lookup по этому ключу, recall находит сводку time-веткой
(«что было на прошлой неделе») и остальными ветками. Неделя без диалогов,
но с измерениями (вес, зал, сон) — тоже неделя жизни: агрегаты рядов входят
в промпт сводки и сами по себе достаточны для её генерации.
"""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from loguru import logger

from bestfiend.memory.measurements.render import render_aggregates
from bestfiend.memory.notes.contracts import NoteDraft
from bestfiend.memory.operation_log import MemoryOperation
from bestfiend.memory.sleep_time.context import (
    SleepContext,
    derive_span,
    invoke_structured,
    try_embed,
)
from bestfiend.memory.sleep_time.period_summaries.prompts import build_summary_messages
from bestfiend.memory.sleep_time.period_summaries.schemas import PeriodSummaryOutput


async def run_period_summaries(
    user_id: UUID, ctx: SleepContext, *, now: datetime | None = None
) -> None:
    """Сводит закрытые недели без сводки (свежие приоритетнее, cap на цикл)."""
    now = now or datetime.now(UTC)
    generated = 0
    for week_start in _closed_weeks(now, ctx.settings.sleep_summary_weeks_back):
        if generated >= ctx.settings.sleep_max_summaries_per_cycle:
            return
        try:
            if await _summarize_week(user_id, week_start, ctx):
                generated += 1
        except Exception as exc:  # noqa: BLE001 — сбой недели не валит остальные
            logger.warning(
                "sleep summaries: week {} failed user_id={}: {}",
                week_start.date(),
                user_id,
                exc,
            )


async def _summarize_week(
    user_id: UUID, week_start: datetime, ctx: SleepContext
) -> bool:
    """Одна неделя: скип при существующей сводке или малом числе наблюдений."""
    if await ctx.notes.find_summary(user_id, week_start) is not None:
        return False
    week_end = week_start + timedelta(weeks=1)
    sources = await ctx.notes.observations_in_range(user_id, week_start, week_end)
    aggregates = await ctx.measurements.aggregate(
        user_id, since=week_start, until=week_end
    )
    # Измерения недели заменяют минимум наблюдений: тихая неделя с одними
    # рядами (вес, зал) всё равно заслуживает сводки.
    if len(sources) < ctx.settings.sleep_summary_min_notes and not aggregates:
        return False

    output = await invoke_structured(
        ctx,
        PeriodSummaryOutput,
        build_summary_messages(
            week_start.date().isoformat(),
            (week_end - timedelta(days=1)).date().isoformat(),
            sources,
            measurements_digest=render_aggregates(aggregates) if aggregates else None,
        ),
        user_id=user_id,
        task="summaries",
    )
    if output is None or not output.content.strip():
        return False

    span_start, span_end = derive_span(sources)
    draft = NoteDraft(
        kind="period_summary",
        content=output.content,
        observed_at=datetime.now(UTC),
        event_time=week_start,
        source_turn_start=span_start,
        source_turn_end=span_end,
        embedding=await try_embed(
            ctx, output.content, user_id=user_id, task="summaries"
        ),
    )
    async with ctx.db.transaction() as tx:
        [summary_id] = await ctx.notes.insert_notes(user_id, [draft], executor=tx)
        await ctx.ops.log(
            user_id,
            [
                MemoryOperation(
                    pipeline="sleep",
                    op="add",
                    note_id=summary_id,
                    detail=f"сводка недели {week_start.date().isoformat()}",
                )
            ],
            executor=tx,
        )
    logger.info(
        "sleep summaries: user_id={} week={} sources={}",
        user_id,
        week_start.date(),
        len(sources),
    )
    return True


def _closed_weeks(now: datetime, weeks_back: int) -> list[datetime]:
    """Понедельники закрытых недель, свежие первыми."""
    today = now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    current_week_start = today - timedelta(days=today.weekday())
    return [current_week_start - timedelta(weeks=i) for i in range(1, weeks_back + 1)]
