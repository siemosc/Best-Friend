"""Слияние почти-дублей: пары близких заметок → одна объединённая с провенансом.

Кандидаты — пары активных заметок одного kind с cosine выше порога; LLM решает
батчем (индексы пар, как Reconciler). Исполнение per-pair короткой транзакцией
с revalidation: между precompute и apply пару могла поменять другая операция.
"""

from datetime import UTC, datetime
from uuid import UUID

from loguru import logger

from bestfiend.memory.notes.contracts import Note, NoteDraft
from bestfiend.memory.notes.profile_budget import apply_profile_budget
from bestfiend.memory.operation_log import MemoryOperation
from bestfiend.memory.sleep_time.context import (
    SleepContext,
    derive_span,
    invoke_structured,
    try_embed,
)
from bestfiend.memory.sleep_time.duplicate_merge.prompts import build_merge_messages
from bestfiend.memory.sleep_time.duplicate_merge.schemas import (
    MergeDecision,
    MergeOutput,
)


_MERGE_KINDS = ["fact", "preference", "rule"]


async def run_duplicate_merge(user_id: UUID, ctx: SleepContext) -> None:
    """Один батч решений по дизъюнктным парам почти-дублей."""
    raw_pairs = await ctx.notes.find_near_duplicates(
        user_id,
        kinds=_MERGE_KINDS,
        min_similarity=ctx.settings.sleep_merge_similarity,
        limit=ctx.settings.sleep_merge_max_pairs,
    )
    pairs = _disjoint([(left, right) for left, right, _ in raw_pairs])
    if not pairs:
        return

    output = await invoke_structured(
        ctx,
        MergeOutput,
        build_merge_messages(pairs),
        user_id=user_id,
        task="merge",
    )
    if output is None:
        return

    for decision in output.decisions:
        await _apply_merge_decision(user_id, pairs, decision, ctx)


async def _apply_merge_decision(
    user_id: UUID,
    pairs: list[tuple[Note, Note]],
    decision: MergeDecision,
    ctx: SleepContext,
) -> None:
    """Валидирует и применяет одно решение о слиянии."""
    pair_index = decision.pair_index
    if not 0 <= pair_index < len(pairs):
        logger.warning("sleep merge: pair_index вне диапазона: {}", pair_index)
        return
    if not decision.merge:
        return
    # В заметку уходит текст модели как есть — strip только в проверке пустоты.
    merged_content = decision.merged_content or ""
    if not merged_content.strip():
        logger.warning("sleep merge: merge=true без merged_content — скип")
        return
    left, right = pairs[pair_index]
    try:
        await _apply_merge(user_id, left, right, merged_content, ctx)
    except Exception as exc:  # noqa: BLE001 — сбой пары не валит остальные
        logger.warning("sleep merge: pair apply failed user_id={}: {}", user_id, exc)


async def _apply_merge(
    user_id: UUID, left: Note, right: Note, merged_content: str, ctx: SleepContext
) -> None:
    """Одна пара: подготовка вне транзакции → короткая транзакция с revalidation."""
    entity_union = {
        *(await ctx.notes.entity_ids_of(left.id)),
        *(await ctx.notes.entity_ids_of(right.id)),
    }
    pinned_parent = next((n for n in (left, right) if n.pinned), None)
    span_start, span_end = derive_span([left, right])
    draft = NoteDraft(
        kind=left.kind,
        content=merged_content,
        observed_at=datetime.now(UTC),
        # Общий субъект родителей наследуется; разногласие (возможно только у
        # fact) → None: уверенности нет, а контент уже слит.
        subject=left.subject if left.subject == right.subject else None,
        pinned=pinned_parent is not None,
        pin_section=pinned_parent.pin_section if pinned_parent else None,
        source_turn_start=span_start,
        source_turn_end=span_end,
        entity_ids=tuple(entity_union),
        embedding=await try_embed(ctx, merged_content, user_id=user_id, task="merge"),
    )

    async with ctx.db.transaction() as tx:
        # Revalidation: обе стороны пары всё ещё active — иначе пару уже
        # поменяла другая операция (Reconciler/revise), сливать нечего.
        statuses = await ctx.notes.statuses_of([left.id, right.id], executor=tx)
        if statuses.get(left.id) != "active" or statuses.get(right.id) != "active":
            logger.warning(
                "sleep merge: пара не active при revalidation user_id={} — скип",
                user_id,
            )
            return
        [merged_id] = await ctx.notes.insert_notes(user_id, [draft], executor=tx)
        await ctx.notes.supersede(left.id, merged_id, executor=tx)
        await ctx.notes.supersede(right.id, merged_id, executor=tx)
        ops = [
            MemoryOperation(
                pipeline="sleep", op="merge", note_id=merged_id, target_note_id=left.id
            ),
            MemoryOperation(
                pipeline="sleep", op="merge", note_id=merged_id, target_note_id=right.id
            ),
        ]
        if draft.pinned:
            demoted = await apply_profile_budget(
                user_id,
                notes_repository=ctx.notes,
                settings=ctx.settings,
                executor=tx,
            )
            ops.extend(
                MemoryOperation(pipeline="sleep", op="demote", note_id=note_id)
                for note_id in demoted
            )
        await ctx.ops.log(user_id, ops, executor=tx)
    logger.info("sleep merge: user_id={} merged 2 notes into {}", user_id, merged_id)


def _disjoint(pairs: list[tuple[Note, Note]]) -> list[tuple[Note, Note]]:
    """Greedy-фильтр: каждая заметка участвует максимум в одной паре за цикл.

    Self-join может вернуть пересечения (A-B и A-C) — supersede общей заметки
    дважды невалиден; пары упорядочены по similarity, оставляем сильнейшие.
    """
    seen: set[UUID] = set()
    result: list[tuple[Note, Note]] = []
    for left, right in pairs:
        if left.id in seen or right.id in seen:
            continue
        seen.update((left.id, right.id))
        result.append((left, right))
    return result
