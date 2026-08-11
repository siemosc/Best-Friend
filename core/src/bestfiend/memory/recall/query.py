"""Hybrid recall: vector + FTS + теги сущностей + время → RRF → gate → бюджет.

LLM на пути поиска нет. Четыре ветки кандидатов идут параллельными запросами,
сливаются Reciprocal Rank Fusion, gate отсекает слабые результаты целиком:
пустой recall — первоклассный исход, мусор в контексте хуже его отсутствия.
Заметки журнала и профиля исключены — они уже в контексте.
"""

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final
from uuid import UUID

import asyncpg
from langfuse import get_client
from loguru import logger

from bestfiend.memory.db import MemoryDatabaseClient
from bestfiend.memory.embeddings import MemoryEmbedder
from bestfiend.memory.entities.repository import EntityRepository
from bestfiend.memory.notes.columns import NOTE_COLUMNS_N
from bestfiend.memory.notes.contracts import Note
from bestfiend.memory.notes.row_mapping import row_to_note
from bestfiend.memory.recall.render import render_note_line, render_note_line_with_span
from bestfiend.memory.recall.time_markers import parse_time_range
from bestfiend.memory.settings import MemorySettings
from bestfiend.primitives.tokenizer import count_tokens


# Стандарт RRF из литературы; не настройка — менять не по чему до появления eval.
_RRF_K: Final[int] = 60

# Архив = заметки вне контекста (журнал/профиль уже отрендерены в промпт).
# contradicted видимы: агент видит конфликт с маркером и уточняет у пользователя;
# superseded скрыты — их заменила актуальная версия.
_ARCHIVE_FILTER: Final[str] = (
    "n.user_id = $1 AND n.status IN ('active', 'contradicted') "
    "AND NOT n.in_journal AND NOT n.pinned"
)


@dataclass(frozen=True, slots=True)
class _RecallHits:
    """Результаты параллельных веток поиска."""

    vector: list[tuple[Note, float]]
    fts: list[Note]
    entity: list[Note]
    time: list[Note]

    @property
    def top_similarity(self) -> float:
        """Возвращает максимальную семантическую близость."""
        return self.vector[0][1] if self.vector else 0.0

    @property
    def branch_sizes(self) -> dict[str, int]:
        """Возвращает число результатов каждой ветки."""
        return {
            "vector": len(self.vector),
            "fts": len(self.fts),
            "entity": len(self.entity),
            "time": len(self.time),
        }


async def recall_notes(
    *,
    user_id: UUID,
    query_text: str,
    db: MemoryDatabaseClient,
    embedder: MemoryEmbedder | None,
    entities_repository: EntityRepository,
    settings: MemorySettings,
    recall_budget: int | None = None,
    kinds: list[str] | None = None,
    subjects: list[str] | None = None,
    top_k: int | None = None,
) -> list[Note]:
    """Возвращает заметки для recall-блока; пустой список = gate не прошёл.

    kinds/subjects — фильтры на уровне SQL-веток (до ранжирования, не после:
    иначе нужные заметки вытеснялись бы нерелевантными верхними результатами).
    top_k переопределяет settings.recall_top_k (агентный memory_search).
    """
    limit = settings.recall_candidates_per_branch
    time_range = parse_time_range(query_text, datetime.now(UTC))
    with get_client().start_as_current_observation(
        name="memory.recall",
        as_type="retriever",
        input={
            "query": query_text,
            "kinds": kinds,
            "subjects": subjects,
            "top_k": top_k or settings.recall_top_k,
            "time_range": (
                [time_range[0].isoformat(), time_range[1].isoformat()]
                if time_range
                else None
            ),
        },
        metadata={"user_id": str(user_id)},
    ) as span:
        hits = await _collect_recall_hits(
            db=db,
            embedder=embedder,
            entities_repository=entities_repository,
            user_id=user_id,
            query_text=query_text,
            time_range=time_range,
            limit=limit,
            kinds=kinds,
            subjects=subjects,
        )
        if not _recall_gate_passed(hits, embedder, settings.recall_min_similarity):
            span.update(
                output={
                    "gate_passed": False,
                    "branches": hits.branch_sizes,
                    "top_similarity": round(hits.top_similarity, 3),
                    "notes": [],
                }
            )
            return []

        fused = _rrf_fuse(
            [note for note, _ in hits.vector],
            hits.fts,
            hits.entity,
            hits.time,
        )
        kept = _apply_budget(fused, settings, recall_budget=recall_budget, top_k=top_k)
        span.update(
            output={
                "gate_passed": True,
                "branches": hits.branch_sizes,
                "top_similarity": round(hits.top_similarity, 3),
                "fused_total": len(fused),
                "notes": [render_note_line_with_span(note) for note in kept],
            }
        )
        return kept


async def _collect_recall_hits(
    *,
    db: MemoryDatabaseClient,
    embedder: MemoryEmbedder | None,
    entities_repository: EntityRepository,
    user_id: UUID,
    query_text: str,
    time_range: tuple[datetime, datetime] | None,
    limit: int,
    kinds: list[str] | None,
    subjects: list[str] | None,
) -> _RecallHits:
    """Параллельно выполняет четыре ветки поиска."""
    vector, fts, entity, time = await asyncio.gather(
        _vector_branch(db, embedder, user_id, query_text, limit, kinds, subjects),
        _fts_branch(db, user_id, query_text, limit, kinds, subjects),
        _entity_branch(
            db, entities_repository, user_id, query_text, limit, kinds, subjects
        ),
        _time_branch(db, user_id, time_range, limit, kinds, subjects),
    )
    return _RecallHits(vector=vector, fts=fts, entity=entity, time=time)


def _recall_gate_passed(
    hits: _RecallHits,
    embedder: MemoryEmbedder | None,
    min_similarity: float,
) -> bool:
    """Проверяет достаточность сигналов для выдачи recall."""
    # Уверенный семантический матч ИЛИ сущность ИЛИ временной маркер с
    # попаданием — темпоральный вопрос («что было в марте») почти не имеет
    # уверенного cosine к конкретной заметке. Без vector-ветки гейтом служат
    # entity/time/FTS-сигналы.
    entity_or_time_hit = bool(hits.entity or hits.time)
    if embedder is None:
        return entity_or_time_hit or bool(hits.fts)
    return hits.top_similarity >= min_similarity or entity_or_time_hit


def _optional_filters(
    args: list[object], kinds: list[str] | None, subjects: list[str] | None
) -> str:
    """SQL-хвост опциональных фильтров; параметры дописываются в args.

    Нумерация $N следует за фактической длиной args — фильтры комбинируются
    в любом сочетании без фиксированных индексов.
    """
    clause = ""
    if kinds:
        args.append(kinds)
        clause += f" AND n.kind = ANY(${len(args)})"
    if subjects:
        args.append(subjects)
        clause += f" AND n.subject = ANY(${len(args)})"
    return clause


async def _vector_branch(
    db: MemoryDatabaseClient,
    embedder: MemoryEmbedder | None,
    user_id: UUID,
    query_text: str,
    limit: int,
    kinds: list[str] | None,
    subjects: list[str] | None,
) -> list[tuple[Note, float]]:
    """Семантическая ветка: cosine KNN по embedding (exact scan)."""
    if embedder is None:
        return []
    try:
        query_vector = await embedder.embed_query(query_text)
    except Exception as exc:  # noqa: BLE001 — recall без векторной ветки лучше отказа
        logger.warning("recall: embed_query failed user_id={}: {}", user_id, exc)
        return []
    try:
        args: list[object] = [user_id, query_vector, limit]
        filters = _optional_filters(args, kinds, subjects)
        rows = await db.fetch(
            f"""
            SELECT {NOTE_COLUMNS_N}, 1 - (n.embedding <=> $2) AS similarity
            FROM notes n
            WHERE {_ARCHIVE_FILTER} AND n.embedding IS NOT NULL{filters}
            ORDER BY n.embedding <=> $2
            LIMIT $3
            """,  # nosec B608 — SQL из внутренних констант, значения через $N-параметры
            *args,
        )
    except asyncpg.PostgresError as exc:
        logger.warning("recall: vector branch failed user_id={}: {}", user_id, exc)
        return []
    return [(row_to_note(row), float(row["similarity"])) for row in rows]


async def _fts_branch(
    db: MemoryDatabaseClient,
    user_id: UUID,
    query_text: str,
    limit: int,
    kinds: list[str] | None,
    subjects: list[str] | None,
) -> list[Note]:
    """Лексическая ветка: websearch FTS (имена, идентификаторы, точные слова)."""
    try:
        args: list[object] = [user_id, query_text, limit]
        filters = _optional_filters(args, kinds, subjects)
        rows = await db.fetch(
            f"""
            SELECT {NOTE_COLUMNS_N},
                   ts_rank(n.content_tsv, websearch_to_tsquery('russian', $2)) AS rank
            FROM notes n
            WHERE {_ARCHIVE_FILTER}
              AND n.content_tsv @@ websearch_to_tsquery('russian', $2){filters}
            ORDER BY rank DESC
            LIMIT $3
            """,  # nosec B608 — SQL из внутренних констант, значения через $N-параметры
            *args,
        )
    except asyncpg.PostgresError as exc:
        logger.warning("recall: fts branch failed user_id={}: {}", user_id, exc)
        return []
    return [row_to_note(row) for row in rows]


async def _entity_branch(
    db: MemoryDatabaseClient,
    entities_repository: EntityRepository,
    user_id: UUID,
    query_text: str,
    limit: int,
    kinds: list[str] | None,
    subjects: list[str] | None,
) -> list[Note]:
    """Тег-ветка: заметки сущностей, упомянутых в запросе (свежие сверху)."""
    try:
        entity_ids = await entities_repository.match_in_text(user_id, query_text)
        if not entity_ids:
            return []
        args: list[object] = [user_id, entity_ids, limit]
        filters = _optional_filters(args, kinds, subjects)
        rows = await db.fetch(
            f"""
            SELECT DISTINCT ON (n.id) {NOTE_COLUMNS_N}
            FROM notes n
            JOIN note_entities ne ON ne.note_id = n.id
            WHERE {_ARCHIVE_FILTER} AND ne.entity_id = ANY($2){filters}
            ORDER BY n.id DESC
            LIMIT $3
            """,  # nosec B608 — SQL из внутренних констант, значения через $N-параметры
            *args,
        )
    except asyncpg.PostgresError as exc:
        logger.warning("recall: entity branch failed user_id={}: {}", user_id, exc)
        return []
    return [row_to_note(row) for row in rows]


async def _time_branch(
    db: MemoryDatabaseClient,
    user_id: UUID,
    time_range: tuple[datetime, datetime] | None,
    limit: int,
    kinds: list[str] | None,
    subjects: list[str] | None,
) -> list[Note]:
    """Временная ветка: заметки периода из маркера запроса (свежие сверху).

    COALESCE обязателен: event_time заполняется только при явной привязке
    в тексте — без фоллбэка на observed_at ветка пропустила бы большинство
    заметок. Диапазон полуоткрытый: >= start AND < end.
    """
    if time_range is None:
        return []
    start, end = time_range
    try:
        args: list[object] = [user_id, start, end, limit]
        filters = _optional_filters(args, kinds, subjects)
        rows = await db.fetch(
            f"""
            SELECT {NOTE_COLUMNS_N}
            FROM notes n
            WHERE {_ARCHIVE_FILTER}
              AND COALESCE(n.event_time, n.observed_at) >= $2
              AND COALESCE(n.event_time, n.observed_at) < $3{filters}
            ORDER BY COALESCE(n.event_time, n.observed_at) DESC
            LIMIT $4
            """,  # nosec B608 — SQL из внутренних констант, значения через $N-параметры
            *args,
        )
    except asyncpg.PostgresError as exc:
        logger.warning("recall: time branch failed user_id={}: {}", user_id, exc)
        return []
    return [row_to_note(row) for row in rows]


async def resolve_note_by_statement(
    *,
    user_id: UUID,
    statement: str,
    db: MemoryDatabaseClient,
    embedder: MemoryEmbedder | None,
    settings: MemorySettings,
) -> Note | None:
    """Топ-1 активная заметка, уверенно соответствующая формулировке (memory_revise).

    Уверенность: cosine ≥ recall_min_similarity; без векторного матча — точный
    лексический (все слова формулировки присутствуют, AND-семантика plainto).
    None — уверенного соответствия нет, правка превращается в новую запись.
    """
    with get_client().start_as_current_observation(
        name="memory.resolve_note",
        as_type="retriever",
        input={"statement": statement},
        metadata={"user_id": str(user_id)},
    ) as span:
        if embedder is not None:
            try:
                query_vector = await embedder.embed_query(statement)
                row = await db.fetch_one(
                    f"""
                    SELECT {NOTE_COLUMNS_N}, 1 - (n.embedding <=> $2) AS similarity
                    FROM notes n
                    WHERE n.user_id = $1 AND n.status = 'active'
                      AND n.embedding IS NOT NULL
                    ORDER BY n.embedding <=> $2
                    LIMIT 1
                    """,  # nosec B608 — SQL из внутренних констант, значения через $N-параметры
                    user_id,
                    query_vector,
                )
                if (
                    row is not None
                    and float(row["similarity"]) >= settings.recall_min_similarity
                ):
                    note = row_to_note(row)
                    span.update(
                        output={
                            "matched_by": "vector",
                            "similarity": round(float(row["similarity"]), 3),
                            "note": render_note_line(note),
                        }
                    )
                    return note
            except Exception as exc:  # noqa: BLE001 — деградация до лексического матча
                logger.warning(
                    "revise resolve: vector branch failed user_id={}: {}", user_id, exc
                )
        try:
            row = await db.fetch_one(
                f"""
                SELECT {NOTE_COLUMNS_N},
                       ts_rank(n.content_tsv, plainto_tsquery('russian', $2)) AS rank
                FROM notes n
                WHERE n.user_id = $1 AND n.status = 'active'
                  AND n.content_tsv @@ plainto_tsquery('russian', $2)
                ORDER BY rank DESC
                LIMIT 1
                """,  # nosec B608 — SQL из внутренних констант, значения через $N-параметры
                user_id,
                statement,
            )
        except asyncpg.PostgresError as exc:
            logger.warning("revise resolve: fts failed user_id={}: {}", user_id, exc)
            span.update(output={"matched_by": None})
            return None
        if row is None:
            span.update(output={"matched_by": None})
            return None
        note = row_to_note(row)
        span.update(output={"matched_by": "fts", "note": render_note_line(note)})
        return note


def _rrf_fuse(*ranked_lists: list[Note]) -> list[Note]:
    """Reciprocal Rank Fusion: score(d) = Σ 1/(K + rank). Устойчив к разным шкалам веток."""
    scores: dict[UUID, float] = {}
    notes: dict[UUID, Note] = {}
    for ranked in ranked_lists:
        for rank, note in enumerate(ranked, start=1):
            scores[note.id] = scores.get(note.id, 0.0) + 1.0 / (_RRF_K + rank)
            notes.setdefault(note.id, note)
    ordered = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    return [notes[note_id] for note_id, _ in ordered]


def _apply_budget(
    fused: list[Note],
    settings: MemorySettings,
    *,
    recall_budget: int | None = None,
    top_k: int | None = None,
) -> list[Note]:
    """Режет результат по top_k (или settings.recall_top_k) и токен-бюджету блока.

    recall_budget=None → потолок ctx_recall_cap (агентный/web/probe-вызов вне
    read-раскладки графа).
    """
    budget = recall_budget if recall_budget is not None else settings.ctx_recall_cap
    kept: list[Note] = []
    total = 0
    for note in fused[: top_k or settings.recall_top_k]:
        tokens = count_tokens(render_note_line(note))
        if kept and total + tokens > budget:
            break
        kept.append(note)
        total += tokens
    return kept
