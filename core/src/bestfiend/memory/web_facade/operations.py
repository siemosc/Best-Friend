"""Write-слой HTTP-фасада памяти: ручные операции пользователя над заметками.

Инварианты общие с memory-тулзами (notes/write_service): каждая операция —
одна транзакция с ops-следом (pipeline='ui'), правка контента — supersede-замена
с наследованием места знания, pin-изменения проходят бюджет профиля. Мутации
разрешены только active-заметкам (матрица статусов: superseded/contradicted —
read-only + delete).
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from bestfiend.memory.db import DatabaseExecutor
from bestfiend.memory.embeddings import try_embed
from bestfiend.memory.notes.contracts import Note, NoteDraft, resolve_subject
from bestfiend.memory.notes.write_service import (
    insert_note_with_ops,
    rebalance_profile,
    revise_with_inheritance,
)
from bestfiend.memory.operation_log import MemoryOperation
from bestfiend.memory.runtime import MemoryRuntime
from bestfiend.memory.web_facade.contracts import CreateNoteRequest, UpdateNoteRequest
from bestfiend.memory.web_facade.errors import (
    NoteNotActiveError,
    NoteNotFoundError,
    PinSectionRequiredError,
    SubjectNotEditableError,
)


# Субъект правится только там, где он свободный (см. resolve_subject):
# у preference/rule он прибит инвариантом, у производных не применим.
_SUBJECT_EDITABLE_KINDS = frozenset({"fact", "observation"})


@dataclass(frozen=True, slots=True)
class _NoteUpdate:
    """Итоговые значения PATCH после применения partial-семантики."""

    pinned: bool
    pin_section: str | None
    in_journal: bool
    subject: str | None


async def create_note(
    runtime: MemoryRuntime, user_id: UUID, request: CreateNoteRequest
) -> UUID:
    """Создаёт заметку руками; pin требует секцию. Возвращает id."""
    if request.pin and request.pin_section is None:
        raise PinSectionRequiredError("pin=true требует pin_section")
    draft = NoteDraft(
        kind=request.kind,
        content=request.content,
        observed_at=datetime.now(UTC),
        # Для preference/rule субъект перепишет инвариант границы вставки.
        subject=request.subject,
        pinned=request.pin,
        pin_section=request.pin_section if request.pin else None,
        embedding=await try_embed(
            runtime.embedder, request.content, user_id=user_id, source="memory http"
        ),
    )
    return await insert_note_with_ops(runtime, user_id, draft, pipeline="ui")


async def update_note(
    runtime: MemoryRuntime, user_id: UUID, note_id: UUID, request: UpdateNoteRequest
) -> None:
    """PATCH-правка флагов/субъекта active-заметки; пропуск поля = «не трогать»."""
    fields = request.model_fields_set
    async with runtime.db.transaction() as tx:
        note = await _fetch_note_for_update(runtime, tx, user_id, note_id)
        update = _resolve_note_update(note, request, fields)

        await runtime.notes_repository.update_note_flags(
            note_id,
            user_id,
            subject=update.subject,
            pinned=update.pinned,
            pin_section=update.pin_section,
            in_journal=update.in_journal,
            executor=tx,
        )
        ops = _update_ops(
            note,
            note_id,
            pinned_final=update.pinned,
            section_final=update.pin_section,
            in_journal_final=update.in_journal,
            subject_final=update.subject,
        )
        # Появившийся pin или смена секции меняют наполнение секций профиля.
        if _requires_profile_rebalance(note, update):
            ops.extend(await rebalance_profile(runtime, user_id, tx, pipeline="ui"))
        await runtime.ops_repository.log(user_id, ops, executor=tx)


def _resolve_note_update(
    note: Note,
    request: UpdateNoteRequest,
    fields: set[str],
) -> _NoteUpdate:
    """Применяет partial-семантику PATCH к текущей заметке."""
    _validate_subject_update(note, fields)
    pinned = _resolve_nullable_flag("pinned", request.pinned, note.pinned, fields)
    pin_section = request.pin_section if "pin_section" in fields else note.pin_section
    if pinned and pin_section is None:
        raise PinSectionRequiredError("pinned-заметка требует pin_section")
    if not pinned:
        pin_section = None
    in_journal = _resolve_nullable_flag(
        "in_journal",
        request.in_journal,
        note.in_journal,
        fields,
    )
    subject = (
        resolve_subject(note.kind, request.subject)
        if "subject" in fields
        else note.subject
    )
    return _NoteUpdate(pinned, pin_section, in_journal, subject)


def _validate_subject_update(note: Note, fields: set[str]) -> None:
    """Запрещает менять субъект у видов с фиксированным инвариантом."""
    if "subject" in fields and note.kind not in _SUBJECT_EDITABLE_KINDS:
        raise SubjectNotEditableError(
            f"субъект kind={note.kind} прибит инвариантом, правка невозможна"
        )


def _resolve_nullable_flag(
    field: str,
    requested: bool | None,
    current: bool,
    fields: set[str],
) -> bool:
    """Считает null для NOT NULL-флага эквивалентом пропуска поля."""
    if field not in fields or requested is None:
        return current
    return requested


def _requires_profile_rebalance(note: Note, update: _NoteUpdate) -> bool:
    """Проверяет, меняется ли наполнение закреплённого профиля."""
    return update.pinned and (not note.pinned or update.pin_section != note.pin_section)


def _update_ops(
    note: Note,
    note_id: UUID,
    *,
    pinned_final: bool,
    section_final: str | None,
    in_journal_final: bool,
    subject_final: str | None,
) -> list[MemoryOperation]:
    """Ops-след PATCH-правки: pin/unpin отдельными op, остальное — edit с detail."""
    ops: list[MemoryOperation] = []
    if pinned_final and not note.pinned:
        ops.append(
            MemoryOperation(
                pipeline="ui", op="pin", note_id=note_id, detail=f"pin={section_final}"
            )
        )
    elif note.pinned and not pinned_final:
        ops.append(MemoryOperation(pipeline="ui", op="unpin", note_id=note_id))
    elif pinned_final and section_final != note.pin_section:
        ops.append(
            MemoryOperation(
                pipeline="ui",
                op="edit",
                note_id=note_id,
                detail=f"pin_section: {note.pin_section} → {section_final}",
            )
        )
    if in_journal_final != note.in_journal:
        ops.append(
            MemoryOperation(
                pipeline="ui",
                op="edit",
                note_id=note_id,
                detail=f"in_journal: {note.in_journal} → {in_journal_final}",
            )
        )
    if subject_final != note.subject:
        ops.append(
            MemoryOperation(
                pipeline="ui",
                op="edit",
                note_id=note_id,
                detail=f"subject: {note.subject} → {subject_final}",
            )
        )
    return ops


async def revise_note(
    runtime: MemoryRuntime, user_id: UUID, note_id: UUID, content: str
) -> UUID:
    """Supersede-замена контента active-заметки с наследованием места знания."""
    embedding = await try_embed(
        runtime.embedder, content, user_id=user_id, source="memory http"
    )
    async with runtime.db.transaction() as tx:
        note = await _fetch_note_for_update(runtime, tx, user_id, note_id)
        new_note_id = await revise_with_inheritance(
            runtime,
            user_id,
            note,
            content,
            embedding=embedding,
            pipeline="ui",
            executor=tx,
        )
    return new_note_id


async def delete_note(runtime: MemoryRuntime, user_id: UUID, note_id: UUID) -> None:
    """Hard delete заметки любого статуса; след с клипом контента переживает её."""
    async with runtime.db.transaction() as tx:
        note = await _fetch_note(runtime, tx, user_id, note_id)
        await runtime.notes_repository.hard_delete(note_id, user_id, executor=tx)
        await runtime.ops_repository.log(
            user_id,
            [
                MemoryOperation(
                    pipeline="ui",
                    op="delete",
                    detail=f"deleted {note.kind}: {note.content}",
                )
            ],
            executor=tx,
        )


async def _fetch_note(
    runtime: MemoryRuntime, executor: DatabaseExecutor, user_id: UUID, note_id: UUID
) -> Note:
    """Заметка пользователя в транзакции операции; нет/чужая → 404."""
    note = await runtime.notes_repository.note_by_id(
        user_id, note_id, executor=executor
    )
    if note is None:
        raise NoteNotFoundError(f"note_id={note_id} не найдена")
    return note


async def _fetch_note_for_update(
    runtime: MemoryRuntime, executor: DatabaseExecutor, user_id: UUID, note_id: UUID
) -> Note:
    """Заметка под мутацию: матрица статусов разрешает править только active."""
    note = await _fetch_note(runtime, executor, user_id, note_id)
    if note.status != "active":
        raise NoteNotActiveError(
            f"note_id={note_id} status={note.status}: правка только active-заметок"
        )
    return note
