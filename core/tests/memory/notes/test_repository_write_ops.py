"""Write-хелперы NoteRepository: порядок SQL у hard_delete, маппинг флагов, выборка по id."""

from uuid import uuid4

import pytest

from bestfiend.memory.notes.repository import NoteRepository


class RecordingExecutor:
    """Собирает execute/fetch-вызовы (SQL + параметры) без исполнения."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    async def execute(self, query: str, *args: object) -> str:
        self.calls.append((query, args))
        return "OK"

    async def fetch(self, query: str, *args: object) -> list[object]:
        self.calls.append((query, args))
        return []

    async def fetch_one(self, query: str, *args: object) -> object | None:
        self.calls.append((query, args))
        return None


@pytest.mark.asyncio
async def test_hard_delete_nulls_incoming_refs_before_delete() -> None:
    """Входящие superseded_by-ссылки зануляются ДО DELETE (FK без ON DELETE)."""
    executor = RecordingExecutor()
    repository = NoteRepository(db=object())  # type: ignore[arg-type] — путь executor
    note_id, user_id = uuid4(), uuid4()

    await repository.hard_delete(note_id, user_id, executor=executor)  # type: ignore[arg-type]

    assert "superseded_by = NULL" in executor.calls[0][0]
    assert executor.calls[0][1] == (note_id,)
    assert "DELETE FROM notes" in executor.calls[1][0]
    assert executor.calls[1][1] == (note_id, user_id)


@pytest.mark.asyncio
async def test_update_note_flags_parameter_mapping() -> None:
    """UPDATE флагов: subject/pinned/section/journal идут за (id, user)-ключом."""
    executor = RecordingExecutor()
    repository = NoteRepository(db=object())  # type: ignore[arg-type] — путь executor
    note_id, user_id = uuid4(), uuid4()

    await repository.update_note_flags(
        note_id,
        user_id,
        subject="world",
        pinned=True,
        pin_section="preferences",
        in_journal=False,
        executor=executor,  # type: ignore[arg-type]
    )

    [(query, args)] = executor.calls
    assert "UPDATE notes" in query
    assert args == (note_id, user_id, "world", True, "preferences", False)


@pytest.mark.asyncio
async def test_note_by_id_missing_returns_none() -> None:
    """Отсутствующая строка → None (маппинг в 404 — забота фасада)."""
    executor = RecordingExecutor()
    repository = NoteRepository(db=object())  # type: ignore[arg-type] — путь executor

    note = await repository.note_by_id(uuid4(), uuid4(), executor=executor)  # type: ignore[arg-type]

    assert note is None
    assert "FROM notes" in executor.calls[0][0]
