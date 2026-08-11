"""Карточки сущностей: «всё важное про X» одним плотным документом.

Карточка — та же заметка (kind='entity_card') с тегом сущности: recall находит
её всеми ветками, перегенерация — supersede прежней. Карточка
обычно выигрывает у россыпи заметок за счёт плотности.
"""

from datetime import UTC, datetime
from uuid import UUID

from loguru import logger

from bestfiend.memory.notes.contracts import NoteDraft
from bestfiend.memory.operation_log import MemoryOperation
from bestfiend.memory.sleep_time.context import (
    SleepContext,
    derive_span,
    invoke_structured,
    try_embed,
)
from bestfiend.memory.sleep_time.entity_cards.prompts import build_card_messages
from bestfiend.memory.sleep_time.entity_cards.schemas import EntityCardOutput


async def run_entity_cards(user_id: UUID, ctx: SleepContext) -> None:
    """Перегенерирует карточки горячих сущностей (идемпотентность — в SQL выборки)."""
    entity_ids = await ctx.notes.hot_entities_needing_cards(
        user_id,
        threshold=ctx.settings.sleep_entity_hot_threshold,
        limit=ctx.settings.sleep_max_cards_per_cycle,
    )
    for entity_id in entity_ids:
        try:
            await _generate_card(user_id, entity_id, ctx)
        except Exception as exc:  # noqa: BLE001 — сбой одной карточки не валит остальные
            logger.warning(
                "sleep cards: card failed user_id={} entity_id={}: {}",
                user_id,
                entity_id,
                exc,
            )


async def _generate_card(user_id: UUID, entity_id: UUID, ctx: SleepContext) -> None:
    """Одна карточка: LLM (вне транзакции) → короткая транзакция insert+supersede+ops."""
    entity_name = await ctx.entities.canonical_name_of(entity_id)
    if entity_name is None:
        return
    sources = await ctx.notes.notes_by_entity(
        user_id, entity_id, limit=ctx.settings.sleep_card_source_notes_max
    )
    if not sources:
        return
    previous_card = await ctx.notes.active_card_of(user_id, entity_id)

    output = await invoke_structured(
        ctx,
        EntityCardOutput,
        build_card_messages(entity_name, sources, previous_card),
        user_id=user_id,
        task="cards",
    )
    if output is None or not output.content.strip():
        return

    span_start, span_end = derive_span(sources)
    draft = NoteDraft(
        kind="entity_card",
        content=output.content,
        observed_at=datetime.now(UTC),
        source_turn_start=span_start,
        source_turn_end=span_end,
        entity_ids=(entity_id,),
        embedding=await try_embed(ctx, output.content, user_id=user_id, task="cards"),
    )
    async with ctx.db.transaction() as tx:
        [card_id] = await ctx.notes.insert_notes(user_id, [draft], executor=tx)
        if previous_card is not None:
            await ctx.notes.supersede(previous_card.id, card_id, executor=tx)
            op = MemoryOperation(
                pipeline="sleep",
                op="supersede",
                note_id=card_id,
                target_note_id=previous_card.id,
                detail=f"карточка: {entity_name}",
            )
        else:
            op = MemoryOperation(
                pipeline="sleep",
                op="add",
                note_id=card_id,
                detail=f"карточка: {entity_name}",
            )
        await ctx.ops.log(user_id, [op], executor=tx)
    logger.info(
        "sleep cards: user_id={} entity={} sources={}",
        user_id,
        entity_name,
        len(sources),
    )
