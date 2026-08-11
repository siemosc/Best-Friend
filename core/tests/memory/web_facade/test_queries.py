"""Read-слой фасада: сборка фильтров, пагинация, порядок контекста, лента ops."""

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest

from bestfiend.memory.web_facade.queries import (
    build_notes_filters,
    entity_refs_of_notes,
    list_notes,
    memory_context,
    memory_overview,
    ops_page,
)
from tests.memory.fakes import NoteRepositoryFake, make_note, note_row


class PagingDb:
    """fetch_one → count/агрегаты, fetch → строки; вызовы копятся для ассертов."""

    def __init__(
        self,
        rows: list[dict[str, Any]] | None = None,
        *,
        total: int = 0,
        tag_rows: list[dict[str, Any]] | None = None,
    ) -> None:
        self.rows = rows or []
        self.total = total
        self.tag_rows = tag_rows or []
        self.fetch_calls: list[tuple[str, tuple[object, ...]]] = []
        self.fetch_one_calls: list[tuple[str, tuple[object, ...]]] = []

    async def fetch(self, query: str, *args: object) -> list[dict[str, Any]]:
        self.fetch_calls.append((query, args))
        if "note_entities" in query and "JOIN entities" in query:
            return self.tag_rows
        return self.rows

    async def fetch_one(self, query: str, *args: object) -> dict[str, Any]:
        self.fetch_one_calls.append((query, args))
        return {"total": self.total}


def test_filters_empty_means_no_clause() -> None:
    """Без фильтров — пустой хвост, args нетронуты."""
    user_id = uuid4()
    args: list[object] = [user_id]

    clause = build_notes_filters(
        args,
        kinds=None,
        subjects=None,
        statuses=None,
        pinned=None,
        in_journal=None,
        entity_id=None,
        q=None,
    )

    assert clause == ""
    assert args == [user_id]


def test_filters_all_numbered_incrementally() -> None:
    """Все фильтры: $N растёт строго по порядку добавления в args."""
    entity_id = uuid4()
    args: list[object] = [uuid4()]

    clause = build_notes_filters(
        args,
        kinds=["fact"],
        subjects=["user"],
        statuses=["active"],
        pinned=True,
        in_journal=False,
        entity_id=entity_id,
        q="чай",
    )

    assert "n.kind = ANY($2)" in clause
    assert "n.subject = ANY($3)" in clause
    assert "n.status = ANY($4)" in clause
    assert "n.pinned = $5" in clause
    assert "n.in_journal = $6" in clause
    assert "ne.entity_id = $7" in clause
    assert "n.content ILIKE '%' || $8 || '%'" in clause
    assert args[1:] == [["fact"], ["user"], ["active"], True, False, entity_id, "чай"]


def test_filters_sparse_keep_dense_numbering() -> None:
    """Пропуски фильтров не оставляют дыр в нумерации."""
    args: list[object] = [uuid4()]

    clause = build_notes_filters(
        args,
        kinds=None,
        subjects=["world"],
        statuses=None,
        pinned=None,
        in_journal=None,
        entity_id=None,
        q="сервер",
    )

    assert "n.subject = ANY($2)" in clause
    assert "$3" in clause and "ILIKE" in clause
    assert args[1:] == [["world"], "сервер"]


@pytest.mark.asyncio
async def test_list_notes_pages_with_same_filter() -> None:
    """count и страница идут под одним WHERE; LIMIT/OFFSET — следующие номера."""
    note = make_note("любит чай")
    db = PagingDb(rows=[note_row(note)], total=42)

    notes, total = await list_notes(
        db,  # type: ignore[arg-type] — стаб по контракту
        uuid4(),
        kinds=["fact"],
        limit=10,
        offset=20,
    )

    assert total == 42
    assert [n.id for n in notes] == [note.id]
    [(count_sql, count_args)] = db.fetch_one_calls
    assert "n.kind = ANY($2)" in count_sql
    [(page_sql, page_args)] = db.fetch_calls
    assert "n.kind = ANY($2)" in page_sql
    assert "ORDER BY n.observed_at DESC, n.id DESC" in page_sql
    assert "LIMIT $3 OFFSET $4" in page_sql
    assert page_args[2:] == (10, 20)
    assert count_args == page_args[:2]


@pytest.mark.asyncio
async def test_context_preserves_reader_order() -> None:
    """Контекст отдаёт профиль/журнал ровно в порядке промпт-читалок."""
    first = make_note("ранняя", pinned=True, pin_section="identity")
    second = make_note("поздняя", pinned=True, pin_section="rules")
    journal_tail = make_note("хвост журнала", in_journal=True)
    journal_head = make_note("голова журнала", in_journal=True)
    notes = NoteRepositoryFake(
        journal=[journal_head, journal_tail],
        pinned=[second, first],  # порядок читалки — не хронологический
    )

    profile, journal = await memory_context(notes, uuid4())  # type: ignore[arg-type]

    assert profile == [second, first]  # без пересортировки
    assert journal == [journal_head, journal_tail]


@pytest.mark.asyncio
async def test_entity_refs_grouped_by_note() -> None:
    """Теги пачки заметок группируются по note_id одним запросом."""
    note_a, note_b = uuid4(), uuid4()
    entity_x, entity_y = uuid4(), uuid4()
    db = PagingDb(
        tag_rows=[
            {"note_id": note_a, "id": entity_x, "canonical_name": "Анна"},
            {"note_id": note_a, "id": entity_y, "canonical_name": "Сервер"},
            {"note_id": note_b, "id": entity_x, "canonical_name": "Анна"},
        ]
    )

    refs = await entity_refs_of_notes(db, [note_a, note_b])  # type: ignore[arg-type]

    assert [r.name for r in refs[note_a]] == ["Анна", "Сервер"]
    assert [r.name for r in refs[note_b]] == ["Анна"]
    assert len(db.fetch_calls) == 1


@pytest.mark.asyncio
async def test_ops_page_filters_and_clips() -> None:
    """Фильтр pipelines нумеруется после user_id; контент заметок клипуется."""
    created_at = datetime(2026, 6, 10, 12, 0, tzinfo=UTC)
    long_content = "х" * 300
    db = PagingDb(
        rows=[
            {
                "id": 1,
                "pipeline": "ui",
                "op": "add",
                "note_id": uuid4(),
                "target_note_id": None,
                "detail": None,
                "created_at": created_at,
                "note_content": long_content,
                "target_note_content": None,
            }
        ],
        total=7,
    )

    ops, total = await ops_page(
        db,  # type: ignore[arg-type]
        uuid4(),
        pipelines=["ui", "tool"],
        limit=50,
        offset=0,
    )

    assert total == 7
    [op] = ops
    assert op.note_content is not None
    assert len(op.note_content) == 161  # клип 160 + многоточие
    assert op.note_content.endswith("…")
    [(page_sql, page_args)] = db.fetch_calls
    assert "o.pipeline = ANY($2)" in page_sql
    assert "LIMIT $3 OFFSET $4" in page_sql
    assert page_args[1] == ["ui", "tool"]


class OverviewDb:
    """Маршрутизирует запросы overview по фрагменту SQL."""

    def __init__(self, breakdown: list[dict[str, Any]]) -> None:
        self.breakdown = breakdown

    async def fetch(self, query: str, *args: object) -> list[dict[str, Any]]:
        assert "GROUP BY kind, subject, status" in query
        return self.breakdown

    async def fetch_one(self, query: str, *args: object) -> dict[str, Any]:
        if "FROM entities" in query:
            return {"total": 3}
        return {"journal_count": 5, "pinned_count": 2}


@pytest.mark.asyncio
async def test_overview_counts_active_slices() -> None:
    """by_kind/by_subject считают только active; by_status — всё; NULL-субъект → none."""
    db = OverviewDb(
        [
            {"kind": "fact", "subject": "user", "status": "active", "cnt": 4},
            {"kind": "fact", "subject": None, "status": "active", "cnt": 1},
            {"kind": "rule", "subject": "agent", "status": "superseded", "cnt": 2},
        ]
    )

    overview = await memory_overview(db, uuid4())  # type: ignore[arg-type]

    assert overview.by_kind == {"fact": 5}
    assert overview.by_subject == {"user": 4, "none": 1}
    assert overview.by_status == {"active": 5, "superseded": 2}
    assert overview.journal_count == 5
    assert overview.pinned_count == 2
    assert overview.entities_count == 3
