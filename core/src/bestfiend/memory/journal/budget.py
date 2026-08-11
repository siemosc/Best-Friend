"""Контроль токен-бюджета журнала рабочей памяти."""

from uuid import UUID

from loguru import logger

from bestfiend.memory.db import MemoryDatabaseClient
from bestfiend.memory.notes.contracts import Note
from bestfiend.memory.notes.repository import NoteRepository
from bestfiend.memory.operation_log import (
    MemoryOperation,
    MemoryOperationLogRepository,
)
from bestfiend.memory.recall.render import render_note_line
from bestfiend.memory.reflector.service import ReflectorService
from bestfiend.memory.settings import MemorySettings
from bestfiend.primitives.tokenizer import count_tokens


async def apply_journal_budget(
    user_id: UUID,
    *,
    db: MemoryDatabaseClient,
    notes_repository: NoteRepository,
    ops_repository: MemoryOperationLogRepository,
    settings: MemorySettings,
    reflector: ReflectorService | None,
) -> None:
    """Сжимает переполненный журнал и применяет FIFO-страховку."""
    journal = await notes_repository.journal_notes(user_id)
    if _journal_token_count(journal) <= settings.journal_token_budget:
        return
    if reflector is not None and await reflector.consolidate(user_id, journal):
        journal = await notes_repository.journal_notes(user_id)

    evict_ids = _select_journal_evictions(
        journal,
        token_budget=settings.journal_token_budget,
    )
    if not evict_ids:
        return
    async with db.transaction() as tx:
        await notes_repository.evict_from_journal(evict_ids, executor=tx)
        await ops_repository.log(
            user_id,
            [
                MemoryOperation(pipeline="observer", op="evict", note_id=note_id)
                for note_id in evict_ids
            ],
            executor=tx,
        )
    logger.info(
        "Journal budget: user_id={} evicted={}",
        user_id,
        len(evict_ids),
    )


def _journal_token_count(journal: list[Note]) -> int:
    """Возвращает стоимость рендера журнала в токенах."""
    return sum(count_tokens(render_note_line(note)) for note in journal)


def _select_journal_evictions(
    journal: list[Note],
    *,
    token_budget: int,
) -> list[UUID]:
    """Выбирает заметки для вытеснения по весу и времени наблюдения."""
    tokens_by_id = {note.id: count_tokens(render_note_line(note)) for note in journal}
    total = sum(tokens_by_id.values())
    if total <= token_budget:
        return []
    evict_ids: list[UUID] = []
    for note in sorted(
        journal, key=lambda item: (item.journal_weight, item.observed_at)
    ):
        if total <= token_budget:
            break
        evict_ids.append(note.id)
        total -= tokens_by_id[note.id]
    return evict_ids
