"""Write-слой фасада: ops-след pipeline='ui', supersede-правка, матрица статусов."""

import dataclasses
from uuid import uuid4

import pytest

from bestfiend.memory.notes.contracts import Note
from bestfiend.memory.settings import MemorySettings
from bestfiend.memory.web_facade.contracts import CreateNoteRequest, UpdateNoteRequest
from bestfiend.memory.web_facade.errors import (
    NoteNotActiveError,
    NoteNotFoundError,
    PinSectionRequiredError,
    SubjectNotEditableError,
)
from bestfiend.memory.web_facade.operations import (
    create_note,
    delete_note,
    revise_note,
    update_note,
)
from tests.memory.fakes import (
    NoteRepositoryFake,
    OperationLogRepositoryFake,
    TransactionalDatabaseFake,
    make_note,
    make_web_facade_memory_runtime,
)


USER_ID = uuid4()


def _note(content: str, **kwargs: object) -> Note:
    """Заметка, принадлежащая пользователю теста (стаб сверяет user_id)."""
    return dataclasses.replace(make_note(content, **kwargs), user_id=USER_ID)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_create_note_logs_add() -> None:
    """Создание без pin: draft уходит в репозиторий, ops add с pipeline='ui'."""
    notes, ops = NoteRepositoryFake(), OperationLogRepositoryFake()
    runtime = make_web_facade_memory_runtime(notes=notes, operation_log=ops)

    note_id = await create_note(
        runtime,
        USER_ID,
        CreateNoteRequest(kind="fact", subject="world", content="сервер в подвале"),
    )

    [draft] = notes.inserted
    assert (draft.kind, draft.subject, draft.content) == (
        "fact",
        "world",
        "сервер в подвале",
    )
    assert draft.pinned is False
    [op] = ops.logged
    assert (op.pipeline, op.op, op.note_id) == ("ui", "add", note_id)


@pytest.mark.asyncio
async def test_create_note_pin_requires_section() -> None:
    """pin=true без секции отклоняется до записи."""
    notes = NoteRepositoryFake()
    runtime = make_web_facade_memory_runtime(notes=notes)

    with pytest.raises(PinSectionRequiredError):
        await create_note(
            runtime,
            USER_ID,
            CreateNoteRequest(kind="rule", subject="agent", content="кратко", pin=True),
        )

    assert notes.inserted == []


@pytest.mark.asyncio
async def test_create_note_pin_rebalances_profile() -> None:
    """pin при переполненной секции → демоции бюджета с ops demote."""
    crowded = _note(
        "старая длинная запись профиля",
        kind="preference",
        pinned=True,
        pin_section="preferences",
    )
    notes, ops = NoteRepositoryFake(pinned=[crowded]), OperationLogRepositoryFake()
    runtime = make_web_facade_memory_runtime(
        notes=notes,
        operation_log=ops,
        settings=MemorySettings(profile_section_token_budget=1),
    )

    await create_note(
        runtime,
        USER_ID,
        CreateNoteRequest(
            kind="preference",
            subject="user",
            content="любит чай",
            pin=True,
            pin_section="preferences",
        ),
    )

    assert notes.demoted_ids != []
    demote_ops = ops.logged_ops("demote")
    assert {op.note_id for op in demote_ops} == set(notes.demoted_ids)
    assert all(op.pipeline == "ui" for op in demote_ops)


@pytest.mark.asyncio
async def test_update_subject_fixed_kind_rejected() -> None:
    """Субъект rule прибит инвариантом — PATCH отклоняется."""
    note = _note("отвечать кратко", kind="rule", subject="agent")
    runtime = make_web_facade_memory_runtime(
        notes=NoteRepositoryFake(by_id={note.id: note})
    )

    with pytest.raises(SubjectNotEditableError):
        await update_note(runtime, USER_ID, note.id, UpdateNoteRequest(subject="user"))


@pytest.mark.asyncio
async def test_update_subject_fact_applies_with_edit_op() -> None:
    """Субъект fact правится: флаги уходят в репозиторий + ops edit с переходом."""
    note = _note("сервер в подвале", kind="fact", subject="user")
    notes, ops = (
        NoteRepositoryFake(by_id={note.id: note}),
        OperationLogRepositoryFake(),
    )
    runtime = make_web_facade_memory_runtime(notes=notes, operation_log=ops)

    await update_note(runtime, USER_ID, note.id, UpdateNoteRequest(subject="world"))

    [update] = notes.flag_updates
    assert update["subject"] == "world"
    [op] = ops.logged_ops("edit")
    assert op.detail == "subject: user → world"


@pytest.mark.asyncio
async def test_update_pin_and_journal_log_separate_ops() -> None:
    """Появление pin → op pin; перевод журнала → op edit; бюджет отработал."""
    note = _note("любит чай", kind="preference", subject="user")
    db = TransactionalDatabaseFake()
    notes, ops = (
        NoteRepositoryFake(by_id={note.id: note}),
        OperationLogRepositoryFake(),
    )
    runtime = make_web_facade_memory_runtime(db=db, notes=notes, operation_log=ops)

    await update_note(
        runtime,
        USER_ID,
        note.id,
        UpdateNoteRequest(pinned=True, pin_section="preferences", in_journal=True),
    )

    [update] = notes.flag_updates
    assert (update["pinned"], update["pin_section"], update["in_journal"]) == (
        True,
        "preferences",
        True,
    )
    # Правка флагов идёт в транзакции операции.
    assert notes.flag_update_executors == [db.transactions[0]]
    [pin_op] = ops.logged_ops("pin")
    assert pin_op.detail == "pin=preferences"
    [edit_op] = ops.logged_ops("edit")
    assert edit_op.detail == "in_journal: False → True"
    assert notes.pinned_executors != []  # бюджет профиля пересчитан в транзакции


@pytest.mark.asyncio
async def test_update_unpin_clears_section() -> None:
    """Снятие pin зануляет секцию и логирует unpin."""
    note = _note("любит чай", kind="preference", pinned=True, pin_section="preferences")
    notes, ops = (
        NoteRepositoryFake(by_id={note.id: note}),
        OperationLogRepositoryFake(),
    )
    runtime = make_web_facade_memory_runtime(notes=notes, operation_log=ops)

    await update_note(runtime, USER_ID, note.id, UpdateNoteRequest(pinned=False))

    [update] = notes.flag_updates
    assert (update["pinned"], update["pin_section"]) == (False, None)
    assert len(ops.logged_ops("unpin")) == 1


@pytest.mark.asyncio
async def test_update_pin_requires_section() -> None:
    """pin без секции (и без уже стоящей) отклоняется."""
    note = _note("факт", kind="fact")
    runtime = make_web_facade_memory_runtime(
        notes=NoteRepositoryFake(by_id={note.id: note})
    )

    with pytest.raises(PinSectionRequiredError):
        await update_note(runtime, USER_ID, note.id, UpdateNoteRequest(pinned=True))


@pytest.mark.asyncio
async def test_update_missing_note_not_found() -> None:
    """PATCH несуществующей/чужой заметки → 404-ошибка домена."""
    runtime = make_web_facade_memory_runtime()

    with pytest.raises(NoteNotFoundError):
        await update_note(runtime, USER_ID, uuid4(), UpdateNoteRequest(in_journal=True))


@pytest.mark.parametrize("status", ["superseded", "contradicted"])
@pytest.mark.asyncio
async def test_update_not_active_rejected(status: str) -> None:
    """Матрица статусов: PATCH разрешён только active-заметкам."""
    note = _note("устаревшее", status=status)
    runtime = make_web_facade_memory_runtime(
        notes=NoteRepositoryFake(by_id={note.id: note})
    )

    with pytest.raises(NoteNotActiveError):
        await update_note(runtime, USER_ID, note.id, UpdateNoteRequest(in_journal=True))


@pytest.mark.asyncio
async def test_revise_inherits_place_of_knowledge() -> None:
    """Правка контента наследует kind/subject/pin/теги и supersede'ит оригинал."""
    tag = uuid4()
    note = _note(
        "любит кофе",
        kind="preference",
        subject="user",
        pinned=True,
        pin_section="preferences",
    )
    notes = NoteRepositoryFake(by_id={note.id: note}, entity_tags={note.id: [tag]})
    ops = OperationLogRepositoryFake()
    runtime = make_web_facade_memory_runtime(notes=notes, operation_log=ops)

    new_id = await revise_note(runtime, USER_ID, note.id, "любит чай, не кофе")

    [(draft, inserted_id)] = notes.inserted_with_ids
    assert inserted_id == new_id
    assert (draft.kind, draft.subject) == ("preference", "user")
    assert (draft.pinned, draft.pin_section) == (True, "preferences")
    assert draft.entity_ids == (tag,)
    assert draft.content == "любит чай, не кофе"
    assert notes.superseded == [(note.id, new_id)]
    [revise_op] = ops.logged_ops("revise")
    assert (revise_op.note_id, revise_op.target_note_id) == (new_id, note.id)


@pytest.mark.parametrize("status", ["superseded", "contradicted"])
@pytest.mark.asyncio
async def test_revise_not_active_rejected(status: str) -> None:
    """Матрица статусов: revise разрешён только active-заметкам."""
    note = _note("устаревшее", status=status)
    notes = NoteRepositoryFake(by_id={note.id: note})
    runtime = make_web_facade_memory_runtime(notes=notes)

    with pytest.raises(NoteNotActiveError):
        await revise_note(runtime, USER_ID, note.id, "новый текст")

    assert notes.inserted == []


@pytest.mark.asyncio
async def test_delete_goes_through_repository_with_trace() -> None:
    """Hard delete уходит в репозиторий в транзакции операции; ops-след без note_id.

    Порядок SQL (занулить входящие superseded_by → DELETE) — инвариант
    NoteRepository.hard_delete, его тест — в notes/test_repository_write_ops.
    """
    note = _note("приватное", kind="fact")
    db = TransactionalDatabaseFake()
    notes, ops = (
        NoteRepositoryFake(by_id={note.id: note}),
        OperationLogRepositoryFake(),
    )
    runtime = make_web_facade_memory_runtime(db=db, notes=notes, operation_log=ops)

    await delete_note(runtime, USER_ID, note.id)

    assert notes.hard_deleted == [(note.id, USER_ID)]
    assert notes.hard_delete_executors == [db.transactions[0]]
    [op] = ops.logged_ops("delete")
    assert op.note_id is None  # след переживает заметку
    assert op.detail is not None
    assert "fact" in op.detail and "приватное" in op.detail


@pytest.mark.asyncio
async def test_delete_allows_any_status() -> None:
    """Delete не требует active — superseded удаляется."""
    note = _note("старая версия", status="superseded")
    notes = NoteRepositoryFake(by_id={note.id: note})
    runtime = make_web_facade_memory_runtime(notes=notes)

    await delete_note(runtime, USER_ID, note.id)

    assert notes.hard_deleted == [(note.id, USER_ID)]


@pytest.mark.asyncio
async def test_delete_foreign_note_not_found() -> None:
    """Заметка другого пользователя невидима для операции."""
    foreign = make_note("чужое")  # user_id = случайный, не USER_ID
    runtime = make_web_facade_memory_runtime(
        notes=NoteRepositoryFake(by_id={foreign.id: foreign})
    )

    with pytest.raises(NoteNotFoundError):
        await delete_note(runtime, USER_ID, foreign.id)
