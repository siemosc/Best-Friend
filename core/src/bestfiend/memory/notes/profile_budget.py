"""Бюджет профиля: демоция pinned-заметок при переполнении секции.

Общий helper для ВСЕХ путей, создающих/меняющих pinned (persist Observer'а,
memory_save с pin, memory_revise с наследованием pin) — бюджет секций
держится независимо от того, кто положил заметку в профиль. Демоция ≠ потеря:
заметка остаётся active в архиве и находится recall'ом.
"""

from uuid import UUID

from bestfiend.memory.db import DatabaseExecutor
from bestfiend.memory.notes.contracts import Note
from bestfiend.memory.notes.repository import NoteRepository
from bestfiend.memory.settings import MemorySettings
from bestfiend.primitives.tokenizer import count_tokens


async def apply_profile_budget(
    user_id: UUID,
    *,
    notes_repository: NoteRepository,
    settings: MemorySettings,
    executor: DatabaseExecutor,
) -> list[UUID]:
    """Демоцирует наименее используемые pinned-заметки, пока секции не влезут в бюджет.

    Работает в транзакции вызывающего (атомарно с операцией, добавившей pin).
    Возвращает id демоцированных заметок — ops-лог пишет вызывающий со своим pipeline.
    """
    pinned = await notes_repository.pinned_notes(user_id, executor=executor)
    if not pinned:
        return []

    by_section: dict[str | None, list[Note]] = {}
    for note in pinned:
        by_section.setdefault(note.pin_section, []).append(note)

    demote_ids: list[UUID] = []
    budget = settings.profile_section_token_budget
    for section_notes in by_section.values():
        demote_ids.extend(_select_section_demotions(section_notes, budget))

    await notes_repository.demote_from_profile(demote_ids, executor=executor)
    return demote_ids


def _select_section_demotions(section_notes: list[Note], budget: int) -> list[UUID]:
    """Кандидаты на демоцию секции: (use_count asc, observed_at asc), пока рендер не влез."""
    tokens_by_id = {note.id: count_tokens(note.content) for note in section_notes}
    total = sum(tokens_by_id.values())
    if total <= budget:
        return []
    demote_ids: list[UUID] = []
    for note in sorted(section_notes, key=lambda n: (n.use_count, n.observed_at)):
        if total <= budget:
            break
        demote_ids.append(note.id)
        total -= tokens_by_id[note.id]
    return demote_ids
