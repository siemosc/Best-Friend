"""Автопробы: вопрос с известным ответом → боевой recall → hit/rank в memory_probes.

Метрика качества retrieval, собираемая в простое: деградация recall видна
SQL-запросом по memory_probes до того, как её заметит пользователь.
"""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from loguru import logger

from bestfiend.memory.notes.contracts import Note
from bestfiend.memory.recall.query import recall_notes
from bestfiend.memory.sleep_time.context import SleepContext, invoke_structured
from bestfiend.memory.sleep_time.probes.prompts import build_probe_messages
from bestfiend.memory.sleep_time.probes.repository import ProbeRepository
from bestfiend.memory.sleep_time.probes.schemas import ProbeOutput


async def run_probes(
    user_id: UUID, ctx: SleepContext, probes_repository: ProbeRepository
) -> None:
    """Пробует recall на свежих заметках; сбой пробы скипает её, не цикл."""
    since = datetime.now(UTC) - timedelta(days=ctx.settings.sleep_probe_recent_days)
    notes = await ctx.notes.recent_active_sample(
        user_id, since=since, limit=ctx.settings.sleep_max_probes_per_cycle
    )
    for note in notes:
        try:
            await _probe_one(user_id, note, ctx, probes_repository)
        except Exception as exc:  # noqa: BLE001 — сбой пробы не валит цикл
            logger.warning("sleep probes: probe failed user_id={}: {}", user_id, exc)


async def _probe_one(
    user_id: UUID,
    note: Note,
    ctx: SleepContext,
    probes_repository: ProbeRepository,
) -> None:
    """Одна проба: вопрос от LLM → боевой recall → позиция заметки в выдаче."""
    output = await invoke_structured(
        ctx,
        ProbeOutput,
        build_probe_messages(note),
        user_id=user_id,
        task="probes",
    )
    if output is None or not output.question.strip():
        return
    found = await recall_notes(
        user_id=user_id,
        query_text=output.question,
        db=ctx.db,
        embedder=ctx.embedder,
        entities_repository=ctx.entities,
        settings=ctx.settings,
    )
    rank = next(
        (index for index, hit in enumerate(found, start=1) if hit.id == note.id),
        None,
    )
    await probes_repository.record(
        user_id,
        question=output.question,
        expected_note_id=note.id,
        hit=rank is not None,
        rank=rank,
    )
    logger.info(
        "sleep probes: user_id={} hit={} rank={}", user_id, rank is not None, rank
    )
