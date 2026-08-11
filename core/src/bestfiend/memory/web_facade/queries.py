"""Read-слой HTTP-фасада памяти: листинг, контекст, сущности, ops-лента, счётчики.

UI-специфичные выборки (пагинация, динамические фильтры) живут здесь, а не в
NoteRepository — пайплайны памяти ими не пользуются. Контекст — наоборот,
делегат в промпт-читалки репозитория: пользователь видит то же, что модель.
"""

from typing import Final
from uuid import UUID

import asyncpg

from bestfiend.memory.db import MemoryDatabaseClient
from bestfiend.memory.notes.columns import NOTE_COLUMNS_N
from bestfiend.memory.notes.contracts import Note
from bestfiend.memory.notes.repository import NoteRepository
from bestfiend.memory.notes.row_mapping import row_to_note
from bestfiend.memory.web_facade.contracts import (
    EntityView,
    MemoryOperationView,
    MemoryOverviewResponse,
    NoteEntityRef,
    NoteView,
    note_view,
)
from bestfiend.memory.web_facade.errors import NoteNotFoundError


# Потолок клипа контента в ленте операций — контекст, не дамп заметки.
_OP_CONTENT_CLIP_CHARS: Final[int] = 160


def build_notes_filters(
    args: list[object],
    *,
    kinds: list[str] | None,
    subjects: list[str] | None,
    statuses: list[str] | None,
    pinned: bool | None,
    in_journal: bool | None,
    entity_id: UUID | None,
    q: str | None,
) -> str:
    """Собирает WHERE-хвост листинга; нумерация $N инкрементальна по len(args)."""
    clause = ""
    if kinds:
        args.append(kinds)
        clause += f" AND n.kind = ANY(${len(args)})"
    if subjects:
        args.append(subjects)
        clause += f" AND n.subject = ANY(${len(args)})"
    if statuses:
        args.append(statuses)
        clause += f" AND n.status = ANY(${len(args)})"
    if pinned is not None:
        args.append(pinned)
        clause += f" AND n.pinned = ${len(args)}"
    if in_journal is not None:
        args.append(in_journal)
        clause += f" AND n.in_journal = ${len(args)}"
    if entity_id is not None:
        args.append(entity_id)
        clause += (
            " AND EXISTS (SELECT 1 FROM note_entities ne"  # nosec B608 — SQL из внутренних констант, значения через $N-параметры
            f" WHERE ne.note_id = n.id AND ne.entity_id = ${len(args)})"
        )
    if q:
        args.append(q)
        clause += f" AND n.content ILIKE '%' || ${len(args)} || '%'"
    return clause


async def list_notes(
    db: MemoryDatabaseClient,
    user_id: UUID,
    *,
    kinds: list[str] | None = None,
    subjects: list[str] | None = None,
    statuses: list[str] | None = None,
    pinned: bool | None = None,
    in_journal: bool | None = None,
    entity_id: UUID | None = None,
    q: str | None = None,
    limit: int,
    offset: int,
) -> tuple[list[Note], int]:
    """Страница заметок (свежие сверху) + total под тем же фильтром."""
    args: list[object] = [user_id]
    filters = build_notes_filters(
        args,
        kinds=kinds,
        subjects=subjects,
        statuses=statuses,
        pinned=pinned,
        in_journal=in_journal,
        entity_id=entity_id,
        q=q,
    )
    where = f"n.user_id = $1{filters}"
    count_row = await db.fetch_one(
        f"SELECT count(*) AS total FROM notes n WHERE {where}",  # nosec B608 — SQL из внутренних констант, значения через $N-параметры
        *args,
    )
    total = int(count_row["total"]) if count_row is not None else 0
    page_args = [*args, limit, offset]
    rows = await db.fetch(
        f"""
        SELECT {NOTE_COLUMNS_N}
        FROM notes n
        WHERE {where}
        ORDER BY n.observed_at DESC, n.id DESC
        LIMIT ${len(args) + 1} OFFSET ${len(args) + 2}
        """,  # nosec B608 — SQL из внутренних констант, значения через $N-параметры
        *page_args,
    )
    return [row_to_note(row) for row in rows], total


async def entity_refs_of_notes(
    db: MemoryDatabaseClient, note_ids: list[UUID]
) -> dict[UUID, list[NoteEntityRef]]:
    """Теги пачки заметок одним запросом (без N+1 в листинге)."""
    if not note_ids:
        return {}
    rows = await db.fetch(
        """
        SELECT ne.note_id, e.id, e.canonical_name
        FROM note_entities ne
        JOIN entities e ON e.id = ne.entity_id
        WHERE ne.note_id = ANY($1)
        ORDER BY e.canonical_name ASC
        """,
        note_ids,
    )
    refs: dict[UUID, list[NoteEntityRef]] = {}
    for row in rows:
        refs.setdefault(row["note_id"], []).append(
            NoteEntityRef(id=row["id"], name=row["canonical_name"])
        )
    return refs


async def notes_with_refs(
    db: MemoryDatabaseClient, notes: list[Note]
) -> list[NoteView]:
    """Собирает представления заметок вместе с их тегами сущностей."""
    refs = await entity_refs_of_notes(db, [note.id for note in notes])
    return [note_view(note, refs.get(note.id, [])) for note in notes]


async def note_view_by_id(
    db: MemoryDatabaseClient,
    notes_repository: NoteRepository,
    user_id: UUID,
    note_id: UUID,
) -> NoteView:
    """Возвращает свежее представление заметки; отсутствие — доменная ошибка."""
    note = await notes_repository.note_by_id(user_id, note_id)
    if note is None:
        raise NoteNotFoundError(f"note_id={note_id} не найдена")
    [view] = await notes_with_refs(db, [note])
    return view


async def memory_context(
    notes_repository: NoteRepository, user_id: UUID
) -> tuple[list[Note], list[Note]]:
    """Профиль и журнал теми же читалками и в том же порядке, что промпт-рендер."""
    profile = await notes_repository.pinned_notes(user_id)
    journal = await notes_repository.journal_notes(user_id)
    return profile, journal


async def memory_overview(
    db: MemoryDatabaseClient, user_id: UUID
) -> MemoryOverviewResponse:
    """Счётчики шапки: разрезы active-заметок, статусы, журнал/профиль, сущности."""
    breakdown_rows = await db.fetch(
        """
        SELECT kind, subject, status, count(*) AS cnt
        FROM notes
        WHERE user_id = $1
        GROUP BY kind, subject, status
        """,
        user_id,
    )
    by_kind: dict[str, int] = {}
    by_subject: dict[str, int] = {}
    by_status: dict[str, int] = {}
    for row in breakdown_rows:
        cnt = int(row["cnt"])
        by_status[row["status"]] = by_status.get(row["status"], 0) + cnt
        if row["status"] != "active":
            continue
        by_kind[row["kind"]] = by_kind.get(row["kind"], 0) + cnt
        subject_key = row["subject"] if row["subject"] is not None else "none"
        by_subject[subject_key] = by_subject.get(subject_key, 0) + cnt
    flags_row = await db.fetch_one(
        """
        SELECT
            count(*) FILTER (WHERE in_journal AND status = 'active') AS journal_count,
            count(*) FILTER (WHERE pinned AND status = 'active') AS pinned_count
        FROM notes
        WHERE user_id = $1
        """,
        user_id,
    )
    entities_row = await db.fetch_one(
        "SELECT count(*) AS total FROM entities WHERE user_id = $1",
        user_id,
    )
    return MemoryOverviewResponse(
        by_kind=by_kind,
        by_subject=by_subject,
        by_status=by_status,
        journal_count=int(flags_row["journal_count"]) if flags_row else 0,
        pinned_count=int(flags_row["pinned_count"]) if flags_row else 0,
        entities_count=int(entities_row["total"]) if entities_row else 0,
    )


async def list_entities_with_counts(
    db: MemoryDatabaseClient, user_id: UUID
) -> list[EntityView]:
    """Сущности с алиасами и числом активных заметок (для секции «Сущности»)."""
    rows = await db.fetch(
        """
        SELECT e.id, e.canonical_name,
               COALESCE(array_agg(DISTINCT a.alias) FILTER (WHERE a.alias IS NOT NULL), '{}') AS aliases,
               count(DISTINCT n.id) FILTER (WHERE n.status = 'active') AS notes_count
        FROM entities e
        LEFT JOIN entity_aliases a ON a.entity_id = e.id
        LEFT JOIN note_entities ne ON ne.entity_id = e.id
        LEFT JOIN notes n ON n.id = ne.note_id
        WHERE e.user_id = $1
        GROUP BY e.id, e.canonical_name
        ORDER BY notes_count DESC, e.canonical_name ASC
        """,
        user_id,
    )
    return [
        EntityView(
            id=row["id"],
            canonical_name=row["canonical_name"],
            aliases=sorted(row["aliases"]),
            notes_count=int(row["notes_count"]),
        )
        for row in rows
    ]


_OPS_SELECT: Final[str] = """
SELECT o.id, o.pipeline, o.op, o.note_id, o.target_note_id, o.detail, o.created_at,
       n1.content AS note_content, n2.content AS target_note_content
FROM memory_ops o
LEFT JOIN notes n1 ON n1.id = o.note_id
LEFT JOIN notes n2 ON n2.id = o.target_note_id
"""


async def ops_page(
    db: MemoryDatabaseClient,
    user_id: UUID,
    *,
    pipelines: list[str] | None = None,
    limit: int,
    offset: int,
) -> tuple[list[MemoryOperationView], int]:
    """Страница ленты операций (свежие сверху) + total под тем же фильтром."""
    args: list[object] = [user_id]
    where = "o.user_id = $1"
    if pipelines:
        args.append(pipelines)
        where += f" AND o.pipeline = ANY(${len(args)})"
    count_row = await db.fetch_one(
        f"SELECT count(*) AS total FROM memory_ops o WHERE {where}",  # nosec B608 — SQL из внутренних констант, значения через $N-параметры
        *args,
    )
    total = int(count_row["total"]) if count_row is not None else 0
    rows = await db.fetch(
        f"""
        {_OPS_SELECT}
        WHERE {where}
        ORDER BY o.id DESC
        LIMIT ${len(args) + 1} OFFSET ${len(args) + 2}
        """,
        *args,
        limit,
        offset,
    )
    return [_row_to_op_view(row) for row in rows], total


async def ops_of_note(
    db: MemoryDatabaseClient, user_id: UUID, note_id: UUID
) -> list[MemoryOperationView]:
    """Операции, где заметка была результатом или второй стороной."""
    rows = await db.fetch(
        f"""
        {_OPS_SELECT}
        WHERE o.user_id = $1 AND (o.note_id = $2 OR o.target_note_id = $2)
        ORDER BY o.id DESC
        """,
        user_id,
        note_id,
    )
    return [_row_to_op_view(row) for row in rows]


def _row_to_op_view(row: asyncpg.Record) -> MemoryOperationView:
    """asyncpg row → MemoryOperationView с клипом контента заметок."""
    return MemoryOperationView(
        id=row["id"],
        pipeline=row["pipeline"],
        op=row["op"],
        note_id=row["note_id"],
        target_note_id=row["target_note_id"],
        detail=row["detail"],
        created_at=row["created_at"],
        note_content=_clip(row["note_content"]),
        target_note_content=_clip(row["target_note_content"]),
    )


def _clip(content: str | None) -> str | None:
    """Обрезает контент заметки до потолка ленты."""
    if content is None or len(content) <= _OP_CONTENT_CLIP_CHARS:
        return content
    return content[:_OP_CONTENT_CLIP_CHARS] + "…"
