"""Преобразование строк хранилища в доменные заметки."""

import asyncpg

from bestfiend.memory.notes.contracts import Note


def row_to_note(row: asyncpg.Record, *, prefix: str = "") -> Note:
    """Преобразует строку asyncpg в заметку; prefix задаёт префикс колонок self-join'а."""
    return Note(
        id=row[f"{prefix}id"],
        user_id=row[f"{prefix}user_id"],
        kind=row[f"{prefix}kind"],
        subject=row[f"{prefix}subject"],
        content=row[f"{prefix}content"],
        event_time=row[f"{prefix}event_time"],
        observed_at=row[f"{prefix}observed_at"],
        status=row[f"{prefix}status"],
        pinned=row[f"{prefix}pinned"],
        pin_section=row[f"{prefix}pin_section"],
        in_journal=row[f"{prefix}in_journal"],
        journal_weight=row[f"{prefix}journal_weight"],
        source_turn_start=row[f"{prefix}source_turn_start"],
        source_turn_end=row[f"{prefix}source_turn_end"],
        use_count=row[f"{prefix}use_count"],
    )
