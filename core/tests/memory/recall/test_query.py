"""Recall: gate (порог/entity-hit/пустота), RRF-слияние, токен-бюджет."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest

from bestfiend.memory.notes.contracts import Note
from bestfiend.memory.recall.query import (
    _rrf_fuse,
    recall_notes,
    resolve_note_by_statement,
)
from bestfiend.memory.settings import MemorySettings


def _note(content: str, note_id: UUID | None = None) -> Note:
    return Note(
        id=note_id or uuid4(),
        user_id=uuid4(),
        kind="fact",
        subject=None,
        content=content,
        event_time=None,
        observed_at=datetime(2026, 6, 9, tzinfo=UTC),
        status="active",
        pinned=False,
        pin_section=None,
        in_journal=False,
        journal_weight=1,
        source_turn_start=None,
        source_turn_end=None,
        use_count=0,
    )


def _note_row(note: Note, **extra: Any) -> dict[str, Any]:
    """Note → dict-строка БД (для стаба fetch)."""
    return {
        "id": note.id,
        "user_id": note.user_id,
        "kind": note.kind,
        "subject": note.subject,
        "content": note.content,
        "event_time": note.event_time,
        "observed_at": note.observed_at,
        "status": note.status,
        "pinned": note.pinned,
        "pin_section": note.pin_section,
        "in_journal": note.in_journal,
        "journal_weight": note.journal_weight,
        "source_turn_start": note.source_turn_start,
        "source_turn_end": note.source_turn_end,
        "use_count": note.use_count,
        **extra,
    }


class DbStub:
    """Маршрутизирует fetch по ветке recall (различимы по фрагменту SQL); пишет вызовы."""

    def __init__(
        self,
        vector_rows: list[dict[str, Any]] | None = None,
        fts_rows: list[dict[str, Any]] | None = None,
        entity_rows: list[dict[str, Any]] | None = None,
        time_rows: list[dict[str, Any]] | None = None,
    ) -> None:
        self.vector_rows = vector_rows or []
        self.fts_rows = fts_rows or []
        self.entity_rows = entity_rows or []
        self.time_rows = time_rows or []
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    async def fetch(self, query: str, *args: object) -> list[dict[str, Any]]:
        self.calls.append((query, args))
        if "embedding <=>" in query:
            return self.vector_rows
        if "websearch_to_tsquery" in query:
            return self.fts_rows
        if "note_entities" in query:
            return self.entity_rows
        if "COALESCE(n.event_time, n.observed_at)" in query:
            return self.time_rows
        raise AssertionError(f"неожиданный SQL: {query}")


class EmbedderStub:
    """Фиксированный вектор запроса."""

    async def embed_query(self, text: str) -> list[float]:
        return [1.0, 0.0]


class EntitiesStub:
    """match_in_text с фиксированным ответом."""

    def __init__(self, ids: list[UUID] | None = None) -> None:
        self.ids = ids or []

    async def match_in_text(self, user_id: UUID, text: str) -> list[UUID]:
        return self.ids


def _settings() -> MemorySettings:
    return MemorySettings(recall_min_similarity=0.5, recall_top_k=8)


@pytest.mark.asyncio
async def test_gate_blocks_weak_similarity_without_entity() -> None:
    """max cosine ниже порога и нет entity-hit → пустой результат."""
    weak = _note_row(_note("слабый матч"), similarity=0.3)
    db = DbStub(vector_rows=[weak])

    result = await recall_notes(
        user_id=uuid4(),
        query_text="вопрос",
        db=db,  # type: ignore[arg-type]
        embedder=EmbedderStub(),  # type: ignore[arg-type]
        entities_repository=EntitiesStub(),  # type: ignore[arg-type]
        settings=_settings(),
    )

    assert result == []


@pytest.mark.asyncio
async def test_gate_passes_on_entity_hit_with_weak_similarity() -> None:
    """Entity-hit пропускает блок даже при слабом cosine."""
    weak = _note_row(_note("слабый матч"), similarity=0.3)
    tagged = _note_row(_note("заметка сущности"))
    db = DbStub(vector_rows=[weak], entity_rows=[tagged])

    result = await recall_notes(
        user_id=uuid4(),
        query_text="что там с BestFiend?",
        db=db,  # type: ignore[arg-type]
        embedder=EmbedderStub(),  # type: ignore[arg-type]
        entities_repository=EntitiesStub([uuid4()]),  # type: ignore[arg-type]
        settings=_settings(),
    )

    assert result != []


@pytest.mark.asyncio
async def test_gate_passes_on_strong_similarity() -> None:
    """Уверенный cosine проходит без entity-hit."""
    strong = _note_row(_note("сильный матч"), similarity=0.8)
    db = DbStub(vector_rows=[strong])

    result = await recall_notes(
        user_id=uuid4(),
        query_text="вопрос",
        db=db,  # type: ignore[arg-type]
        embedder=EmbedderStub(),  # type: ignore[arg-type]
        entities_repository=EntitiesStub(),  # type: ignore[arg-type]
        settings=_settings(),
    )

    assert [n.content for n in result] == ["сильный матч"]


@pytest.mark.asyncio
async def test_no_embedder_gate_uses_entity_and_fts() -> None:
    """Без векторной ветки: пустые entity и FTS → пусто; FTS-матч → блок есть."""
    user_id = uuid4()
    empty = await recall_notes(
        user_id=user_id,
        query_text="вопрос",
        embedder=None,
        settings=_settings(),
        db=DbStub(),  # type: ignore[arg-type]
        entities_repository=EntitiesStub(),  # type: ignore[arg-type]
    )
    fts_hit = await recall_notes(
        user_id=user_id,
        query_text="вопрос",
        embedder=None,
        settings=_settings(),
        db=DbStub(fts_rows=[_note_row(_note("лексический матч"), rank=0.9)]),  # type: ignore[arg-type]
        entities_repository=EntitiesStub(),  # type: ignore[arg-type]
    )

    assert empty == []
    assert [n.content for n in fts_hit] == ["лексический матч"]


def test_rrf_double_branch_beats_single() -> None:
    """Заметка из двух веток ранжируется выше одиночных лидеров веток."""
    both = _note("в обеих ветках")
    vector_only = _note("только vector")
    fts_only = _note("только fts")

    fused = _rrf_fuse([vector_only, both], [fts_only, both])

    assert fused[0].id == both.id


@pytest.mark.asyncio
async def test_kinds_filter_applied_in_sql_branches() -> None:
    """kinds доезжает до SQL всех веток (фильтр до ранжирования, не после)."""
    strong = _note_row(_note("факт"), similarity=0.9)
    entity_row = _note_row(_note("тегированная"))
    db = DbStub(vector_rows=[strong], entity_rows=[entity_row])

    await recall_notes(
        user_id=uuid4(),
        query_text="вопрос про BestFiend",
        db=db,  # type: ignore[arg-type]
        embedder=EmbedderStub(),  # type: ignore[arg-type]
        entities_repository=EntitiesStub([uuid4()]),  # type: ignore[arg-type]
        settings=_settings(),
        kinds=["fact", "preference"],
    )

    assert len(db.calls) == 3  # vector + fts + entity
    for query, args in db.calls:
        assert "kind = ANY($4)" in query
        assert args[3] == ["fact", "preference"]


@pytest.mark.asyncio
async def test_no_kinds_means_no_filter_in_sql() -> None:
    """Без kinds SQL не содержит kind-предиката и лишнего параметра."""
    strong = _note_row(_note("факт"), similarity=0.9)
    db = DbStub(vector_rows=[strong])

    await recall_notes(
        user_id=uuid4(),
        query_text="вопрос",
        db=db,  # type: ignore[arg-type]
        embedder=EmbedderStub(),  # type: ignore[arg-type]
        entities_repository=EntitiesStub(),  # type: ignore[arg-type]
        settings=_settings(),
    )

    for query, args in db.calls:
        assert "kind = ANY" not in query
        assert "subject = ANY" not in query
        assert len(args) == 3


@pytest.mark.asyncio
async def test_subjects_filter_applied_in_sql_branches() -> None:
    """subjects доезжает до SQL всех веток (фильтр до ранжирования)."""
    strong = _note_row(_note("про пользователя"), similarity=0.9)
    db = DbStub(vector_rows=[strong])

    await recall_notes(
        user_id=uuid4(),
        query_text="вопрос",
        db=db,  # type: ignore[arg-type]
        embedder=EmbedderStub(),  # type: ignore[arg-type]
        entities_repository=EntitiesStub(),  # type: ignore[arg-type]
        settings=_settings(),
        subjects=["user"],
    )

    assert len(db.calls) == 2  # vector + fts
    for query, args in db.calls:
        assert "subject = ANY($4)" in query
        assert args[3] == ["user"]


@pytest.mark.asyncio
async def test_kinds_and_subjects_filters_combine() -> None:
    """Оба фильтра вместе: kinds = $4, subjects = $5 (нумерация по порядку)."""
    strong = _note_row(_note("факт о мире"), similarity=0.9)
    db = DbStub(vector_rows=[strong])

    await recall_notes(
        user_id=uuid4(),
        query_text="вопрос",
        db=db,  # type: ignore[arg-type]
        embedder=EmbedderStub(),  # type: ignore[arg-type]
        entities_repository=EntitiesStub(),  # type: ignore[arg-type]
        settings=_settings(),
        kinds=["fact"],
        subjects=["world", "agent"],
    )

    for query, args in db.calls:
        assert "kind = ANY($4)" in query
        assert "subject = ANY($5)" in query
        assert args[3] == ["fact"]
        assert args[4] == ["world", "agent"]


@pytest.mark.asyncio
async def test_top_k_override_caps_result() -> None:
    """top_k переопределяет settings.recall_top_k (агентный limit)."""
    rows = [
        _note_row(_note(f"заметка {i}"), similarity=0.9 - i * 0.01) for i in range(6)
    ]
    db = DbStub(vector_rows=rows)

    result = await recall_notes(
        user_id=uuid4(),
        query_text="вопрос",
        db=db,  # type: ignore[arg-type]
        embedder=EmbedderStub(),  # type: ignore[arg-type]
        entities_repository=EntitiesStub(),  # type: ignore[arg-type]
        settings=_settings(),
        top_k=2,
    )

    assert len(result) == 2


@pytest.mark.asyncio
async def test_time_marker_adds_branch_and_passes_gate() -> None:
    """Временной маркер: четвёртая ветка с полуоткрытым диапазоном; time-hit
    пропускает gate при слабом cosine."""
    weak = _note_row(_note("слабый матч"), similarity=0.2)
    dated = _note_row(_note("событие той недели"))
    db = DbStub(vector_rows=[weak], time_rows=[dated])

    result = await recall_notes(
        user_id=uuid4(),
        query_text="что было на прошлой неделе?",
        db=db,  # type: ignore[arg-type]
        embedder=EmbedderStub(),  # type: ignore[arg-type]
        entities_repository=EntitiesStub(),  # type: ignore[arg-type]
        settings=_settings(),
    )

    assert [n.content for n in result if n.content == "событие той недели"]
    time_calls = [
        (q, a) for q, a in db.calls if "COALESCE(n.event_time, n.observed_at)" in q
    ]
    assert len(time_calls) == 1
    query, args = time_calls[0]
    assert ">= $2" in query
    assert "< $3" in query  # полуоткрытый диапазон
    assert args[2] > args[1]  # type: ignore[operator] — (start, end)


@pytest.mark.asyncio
async def test_no_time_marker_keeps_three_branches() -> None:
    """Без маркера time-ветка не делает SQL-вызова (три ветки как в V2)."""
    strong = _note_row(_note("сильный матч"), similarity=0.8)
    db = DbStub(vector_rows=[strong])

    await recall_notes(
        user_id=uuid4(),
        query_text="вопрос без времени",
        db=db,  # type: ignore[arg-type]
        embedder=EmbedderStub(),  # type: ignore[arg-type]
        entities_repository=EntitiesStub(),  # type: ignore[arg-type]
        settings=_settings(),
    )

    assert len(db.calls) == 2  # vector + fts (entity без матча не делает SQL)
    assert all("COALESCE(n.event_time, n.observed_at)" not in q for q, _ in db.calls)


@pytest.mark.asyncio
async def test_archive_filter_keeps_contradicted_visible() -> None:
    """Ветки recall фильтруют по status IN (active, contradicted) — конфликт всплывает."""
    strong = _note_row(_note("сильный матч"), similarity=0.8)
    db = DbStub(vector_rows=[strong])

    await recall_notes(
        user_id=uuid4(),
        query_text="вопрос",
        db=db,  # type: ignore[arg-type]
        embedder=EmbedderStub(),  # type: ignore[arg-type]
        entities_repository=EntitiesStub(),  # type: ignore[arg-type]
        settings=_settings(),
    )

    for query, _ in db.calls:
        assert "status IN ('active', 'contradicted')" in query


class ResolveDbStub:
    """fetch_one-роутер для resolve_note_by_statement: vector / plainto FTS."""

    def __init__(
        self,
        vector_row: dict[str, Any] | None = None,
        fts_row: dict[str, Any] | None = None,
    ) -> None:
        self.vector_row = vector_row
        self.fts_row = fts_row
        self.queries: list[str] = []

    async def fetch_one(self, query: str, *args: object) -> dict[str, Any] | None:
        self.queries.append(query)
        if "embedding <=>" in query:
            return self.vector_row
        if "plainto_tsquery" in query:
            return self.fts_row
        raise AssertionError(f"неожиданный SQL: {query}")


@pytest.mark.asyncio
async def test_resolve_statement_confident_vector_match() -> None:
    """cosine ≥ порога → цель найдена векторной веткой, FTS не нужен."""
    target = _note("отвечает развёрнуто")
    db = ResolveDbStub(vector_row=_note_row(target, similarity=0.8))

    resolved = await resolve_note_by_statement(
        user_id=uuid4(),
        statement="отвечает развёрнуто",
        db=db,  # type: ignore[arg-type]
        embedder=EmbedderStub(),  # type: ignore[arg-type]
        settings=_settings(),
    )

    assert resolved is not None
    assert resolved.id == target.id
    assert len(db.queries) == 1  # FTS-ветка не понадобилась


@pytest.mark.asyncio
async def test_resolve_statement_weak_vector_falls_back_to_fts() -> None:
    """cosine ниже порога → точный лексический матч решает."""
    weak = _note("про другое")
    exact = _note("любит зелёный чай")
    db = ResolveDbStub(
        vector_row=_note_row(weak, similarity=0.3),
        fts_row=_note_row(exact, rank=0.9),
    )

    resolved = await resolve_note_by_statement(
        user_id=uuid4(),
        statement="любит зелёный чай",
        db=db,  # type: ignore[arg-type]
        embedder=EmbedderStub(),  # type: ignore[arg-type]
        settings=_settings(),
    )

    assert resolved is not None
    assert resolved.id == exact.id


@pytest.mark.asyncio
async def test_resolve_statement_no_match_returns_none() -> None:
    """Ни уверенного вектора, ни лексического матча → None (без embedder — сразу FTS)."""
    db = ResolveDbStub()

    resolved = await resolve_note_by_statement(
        user_id=uuid4(),
        statement="нечто невиданное",
        db=db,  # type: ignore[arg-type]
        embedder=None,
        settings=_settings(),
    )

    assert resolved is None
    assert len(db.queries) == 1  # только FTS: векторной ветки без embedder нет


@pytest.mark.asyncio
async def test_budget_caps_result() -> None:
    """Результат режется по top_k и токен-бюджету recall-блока."""
    rows = [
        _note_row(_note(f"заметка {i} " + "слово " * 30), similarity=0.9 - i * 0.01)
        for i in range(10)
    ]
    db = DbStub(vector_rows=rows)
    settings = MemorySettings(recall_min_similarity=0.5, recall_top_k=8)

    result = await recall_notes(
        user_id=uuid4(),
        query_text="вопрос",
        db=db,  # type: ignore[arg-type]
        embedder=EmbedderStub(),  # type: ignore[arg-type]
        entities_repository=EntitiesStub(),  # type: ignore[arg-type]
        settings=settings,
        recall_budget=80,
    )

    assert 0 < len(result) < 10
