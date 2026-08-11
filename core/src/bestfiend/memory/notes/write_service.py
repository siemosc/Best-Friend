"""Общий write-путь заметок для агентных тулз (pipeline='tool') и web-фасада ('ui').

Инварианты в одном месте, не «на дисциплине» писателей: операция — одна
транзакция с ops-следом; revise — supersede-замена с полным наследованием
места знания (kind, subject, журнал, pin, теги); pin-изменения проходят
бюджет профиля. Embedding вызывающий готовит до транзакции — сетевой вызов
не держит соединение пула.
"""

from datetime import UTC, datetime
from uuid import UUID

from bestfiend.memory.db import DatabaseExecutor
from bestfiend.memory.notes.contracts import Note, NoteDraft
from bestfiend.memory.notes.profile_budget import apply_profile_budget
from bestfiend.memory.operation_log import MemoryOperation, OpsAction, OpsPipeline
from bestfiend.memory.runtime import MemoryRuntime


async def insert_note_with_ops(
    runtime: MemoryRuntime,
    user_id: UUID,
    draft: NoteDraft,
    *,
    pipeline: OpsPipeline,
    op: OpsAction = "add",
    detail: str | None = None,
) -> UUID:
    """Одна транзакция: вставка заметки + ops-след + бюджет профиля при pin."""
    if detail is None and op == "add" and draft.pinned:
        detail = f"pin={draft.pin_section}"
    async with runtime.db.transaction() as tx:
        [note_id] = await runtime.notes_repository.insert_notes(
            user_id, [draft], executor=tx
        )
        ops = [
            MemoryOperation(pipeline=pipeline, op=op, note_id=note_id, detail=detail)
        ]
        if draft.pinned:
            ops.extend(await rebalance_profile(runtime, user_id, tx, pipeline=pipeline))
        await runtime.ops_repository.log(user_id, ops, executor=tx)
    return note_id


async def revise_with_inheritance(
    runtime: MemoryRuntime,
    user_id: UUID,
    target: Note,
    corrected_content: str,
    *,
    embedding: list[float] | None,
    pipeline: OpsPipeline,
    executor: DatabaseExecutor,
) -> UUID:
    """Supersede-замена на открытом executor'е: правка не меняет место знания.

    kind, subject, журнал (in_journal/journal_weight), pin и теги наследуются
    от заменяемой заметки. Ops-след op='revise'; pinned-замена проходит бюджет
    профиля. Транзакцию открывает вызывающий — он же решает, что фетчится
    внутри неё (web проверяет статус заметки в той же транзакции).
    """
    inherited_entities = await runtime.notes_repository.entity_ids_of(
        target.id, executor=executor
    )
    draft = NoteDraft(
        kind=target.kind,
        content=corrected_content,
        observed_at=datetime.now(UTC),
        subject=target.subject,
        in_journal=target.in_journal,
        journal_weight=target.journal_weight,
        pinned=target.pinned,
        pin_section=target.pin_section if target.pinned else None,
        entity_ids=tuple(inherited_entities),
        embedding=embedding,
    )
    [new_note_id] = await runtime.notes_repository.insert_notes(
        user_id, [draft], executor=executor
    )
    await runtime.notes_repository.supersede(target.id, new_note_id, executor=executor)
    ops = [
        MemoryOperation(
            pipeline=pipeline,
            op="revise",
            note_id=new_note_id,
            target_note_id=target.id,
        )
    ]
    if draft.pinned:
        ops.extend(
            await rebalance_profile(runtime, user_id, executor, pipeline=pipeline)
        )
    await runtime.ops_repository.log(user_id, ops, executor=executor)
    return new_note_id


async def rebalance_profile(
    runtime: MemoryRuntime,
    user_id: UUID,
    executor: DatabaseExecutor,
    *,
    pipeline: OpsPipeline,
) -> list[MemoryOperation]:
    """Бюджет секций профиля после pin-изменения; возвращает demote-операции."""
    demoted = await apply_profile_budget(
        user_id,
        notes_repository=runtime.notes_repository,
        settings=runtime.memory_settings,
        executor=executor,
    )
    return [
        MemoryOperation(pipeline=pipeline, op="demote", note_id=note_id)
        for note_id in demoted
    ]
