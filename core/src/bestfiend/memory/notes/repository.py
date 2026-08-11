"""PostgreSQL repository для заметок (core.notes + core.note_entities)."""

from datetime import datetime
from uuid import UUID

import asyncpg
from loguru import logger
from uuid6 import uuid7

from bestfiend.memory.db import DatabaseExecutor, MemoryDatabaseClient
from bestfiend.memory.notes.columns import NOTE_COLUMNS, NOTE_COLUMNS_N
from bestfiend.memory.notes.contracts import Note, NoteDraft, resolve_subject
from bestfiend.memory.notes.row_mapping import row_to_note


_INSERT_NOTE_SQL = """
INSERT INTO notes (
    id, user_id, kind, subject, content, event_time, observed_at, status, in_journal,
    journal_weight, pinned, pin_section, source_turn_start, source_turn_end,
    embedding
) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15)
"""

_INSERT_NOTE_ENTITY_SQL = (
    "INSERT INTO note_entities (note_id, entity_id) VALUES ($1, $2) "
    "ON CONFLICT DO NOTHING"
)


class NoteRepository:
    """Доступ к заметкам: вставка с тегами, выборки журнала/профиля, статусы, вытеснение."""

    __slots__ = ("_db",)

    def __init__(self, db: MemoryDatabaseClient) -> None:
        self._db = db

    async def insert_notes(
        self,
        user_id: UUID,
        drafts: list[NoteDraft],
        *,
        executor: DatabaseExecutor | None = None,
    ) -> list[UUID]:
        """Вставляет батч заметок с тегами атомарно. Возвращает id.

        Без executor открывает собственную транзакцию; с executor — пишет в уже
        открытую (атомарный персист прогона Observer: заметки + вытеснение + watermark).
        """
        if not drafts:
            return []
        try:
            if executor is not None:
                return await self._insert_with(executor, user_id, drafts)
            async with self._db.transaction() as tx:
                return await self._insert_with(tx, user_id, drafts)
        except asyncpg.PostgresError:
            logger.exception("NoteRepository: insert_notes failed user_id={}", user_id)
            raise

    @staticmethod
    async def _insert_with(
        executor: DatabaseExecutor, user_id: UUID, drafts: list[NoteDraft]
    ) -> list[UUID]:
        """Вставка батча через готовый executor (внутри транзакции вызывающего).

        Субъект нормализуется здесь, на границе вставки: инвариант
        (preference→user, rule→agent, производные→NULL) держится для любого
        писателя — Observer, тулз, sleep-time и будущих.
        """
        note_ids: list[UUID] = []
        for draft in drafts:
            note_id = uuid7()
            await executor.execute(
                _INSERT_NOTE_SQL,
                note_id,
                user_id,
                draft.kind,
                resolve_subject(draft.kind, draft.subject),
                draft.content,
                draft.event_time,
                draft.observed_at,
                draft.status,
                draft.in_journal,
                draft.journal_weight,
                draft.pinned,
                draft.pin_section,
                draft.source_turn_start,
                draft.source_turn_end,
                draft.embedding,
            )
            for entity_id in draft.entity_ids:
                await executor.execute(_INSERT_NOTE_ENTITY_SQL, note_id, entity_id)
            note_ids.append(note_id)
        return note_ids

    async def journal_notes(
        self, user_id: UUID, *, executor: DatabaseExecutor | None = None
    ) -> list[Note]:
        """Заметки журнала, хронологически (для рендера и оценки бюджета).

        Фильтр status='active' — вторая линия защиты: смена статуса снимает
        in_journal тем же UPDATE, но даже осиротевший флаг в контекст не попадёт.
        """
        rows = await (executor or self._db).fetch(
            f"""
            SELECT {NOTE_COLUMNS}
            FROM notes
            WHERE user_id = $1 AND in_journal AND status = 'active'
            ORDER BY observed_at ASC, id ASC
            """,  # nosec B608 — SQL из внутренних констант, значения через $N-параметры
            user_id,
        )
        return [row_to_note(row) for row in rows]

    async def pinned_notes(
        self, user_id: UUID, *, executor: DatabaseExecutor | None = None
    ) -> list[Note]:
        """Pinned-заметки (профиль), по секциям и времени."""
        rows = await (executor or self._db).fetch(
            f"""
            SELECT {NOTE_COLUMNS}
            FROM notes
            WHERE user_id = $1 AND pinned AND status = 'active'
            ORDER BY pin_section NULLS LAST, observed_at ASC
            """,  # nosec B608 — SQL из внутренних констант, значения через $N-параметры
            user_id,
        )
        return [row_to_note(row) for row in rows]

    async def evict_from_journal(
        self, note_ids: list[UUID], *, executor: DatabaseExecutor | None = None
    ) -> None:
        """Снимает флаг журнала — заметки остаются в архиве и находятся поиском."""
        if not note_ids:
            return
        await (executor or self._db).execute(
            "UPDATE notes SET in_journal = false WHERE id = ANY($1)",
            note_ids,
        )

    async def supersede(
        self,
        old_note_id: UUID,
        new_note_id: UUID,
        *,
        executor: DatabaseExecutor,
    ) -> None:
        """Помечает заметку заменённой: статус + ссылка на замену.

        Инвариант статусов: невалидное знание уходит из контекста немедленно —
        in_journal/pinned снимаются тем же UPDATE.
        """
        await executor.execute(
            """
            UPDATE notes
            SET status = 'superseded', superseded_by = $2,
                in_journal = false, pinned = false
            WHERE id = $1
            """,
            old_note_id,
            new_note_id,
        )

    async def mark_contradicted(
        self, note_id: UUID, *, executor: DatabaseExecutor
    ) -> None:
        """Помечает заметку противоречащей (обе стороны конфликта остаются в recall)."""
        await executor.execute(
            """
            UPDATE notes
            SET status = 'contradicted', in_journal = false, pinned = false
            WHERE id = $1
            """,
            note_id,
        )

    async def demote_from_profile(
        self, note_ids: list[UUID], *, executor: DatabaseExecutor
    ) -> None:
        """Снимает pin (демоция при переполнении секции) — заметка остаётся active в архиве."""
        if not note_ids:
            return
        await executor.execute(
            "UPDATE notes SET pinned = false WHERE id = ANY($1)",
            note_ids,
        )

    async def find_similar(
        self,
        user_id: UUID,
        embedding: list[float],
        *,
        kinds: list[str],
        limit: int,
    ) -> list[tuple[Note, float]]:
        """Соседи кандидата по cosine (active + contradicted) — вход Reconciler'а."""
        rows = await self._db.fetch(
            f"""
            SELECT {NOTE_COLUMNS}, 1 - (embedding <=> $2) AS similarity
            FROM notes
            WHERE user_id = $1 AND status IN ('active', 'contradicted')
              AND kind = ANY($4) AND embedding IS NOT NULL
            ORDER BY embedding <=> $2
            LIMIT $3
            """,  # nosec B608 — SQL из внутренних констант, значения через $N-параметры
            user_id,
            embedding,
            limit,
            kinds,
        )
        return [(row_to_note(row), float(row["similarity"])) for row in rows]

    async def find_by_entities(
        self,
        user_id: UUID,
        entity_ids: list[UUID],
        *,
        kinds: list[str],
        limit: int,
    ) -> list[Note]:
        """Соседи кандидата по общим сущностям (свежие сверху) — вход Reconciler'а."""
        if not entity_ids:
            return []
        rows = await self._db.fetch(
            f"""
            SELECT DISTINCT ON (n.id) {NOTE_COLUMNS_N}
            FROM notes n
            JOIN note_entities ne ON ne.note_id = n.id
            WHERE n.user_id = $1 AND n.status IN ('active', 'contradicted')
              AND n.kind = ANY($4) AND ne.entity_id = ANY($2)
            ORDER BY n.id DESC
            LIMIT $3
            """,  # nosec B608 — SQL из внутренних констант, значения через $N-параметры
            user_id,
            entity_ids,
            limit,
            kinds,
        )
        return [row_to_note(row) for row in rows]

    async def entity_ids_of(
        self, note_id: UUID, *, executor: DatabaseExecutor | None = None
    ) -> list[UUID]:
        """Теги заметки (наследование при revise-замене)."""
        rows = await (executor or self._db).fetch(
            "SELECT entity_id FROM note_entities WHERE note_id = $1",
            note_id,
        )
        return [row["entity_id"] for row in rows]

    async def note_by_id(
        self, user_id: UUID, note_id: UUID, *, executor: DatabaseExecutor | None = None
    ) -> Note | None:
        """Заметка пользователя по id; None — нет или чужая."""
        row = await (executor or self._db).fetch_one(
            f"SELECT {NOTE_COLUMNS} FROM notes WHERE user_id = $1 AND id = $2",  # nosec B608 — SQL из внутренних констант, значения через $N-параметры
            user_id,
            note_id,
        )
        return row_to_note(row) if row is not None else None

    async def update_note_flags(
        self,
        note_id: UUID,
        user_id: UUID,
        *,
        subject: str | None,
        pinned: bool,
        pin_section: str | None,
        in_journal: bool,
        executor: DatabaseExecutor,
    ) -> None:
        """Правка флагов места знания (PATCH web-фасада) одним UPDATE."""
        await executor.execute(
            """
            UPDATE notes
            SET subject = $3, pinned = $4, pin_section = $5, in_journal = $6
            WHERE id = $1 AND user_id = $2
            """,
            note_id,
            user_id,
            subject,
            pinned,
            pin_section,
            in_journal,
        )

    async def hard_delete(
        self, note_id: UUID, user_id: UUID, *, executor: DatabaseExecutor
    ) -> None:
        """Физическое удаление заметки любого статуса.

        Входящие ссылки замен держат FK без ON DELETE — зануляются до DELETE.
        """
        await executor.execute(
            "UPDATE notes SET superseded_by = NULL WHERE superseded_by = $1",
            note_id,
        )
        await executor.execute(
            "DELETE FROM notes WHERE id = $1 AND user_id = $2",
            note_id,
            user_id,
        )

    async def hot_entities_needing_cards(
        self, user_id: UUID, *, threshold: int, limit: int
    ) -> list[UUID]:
        """Сущности, которым нужна (пере)генерация карточки.

        Горячая = ≥ threshold активных заметок с тегом И (карточки нет ИЛИ есть
        заметки свежее карточки). Свежесть — сравнение по notes.id: id = uuid7,
        монотонный по времени создания; created_at наружу не выносится.
        bool_or вместо max: у PostgreSQL нет агрегата max(uuid), сравнение есть.
        """
        rows = await self._db.fetch(
            """
            SELECT ne.entity_id
            FROM note_entities ne
            JOIN notes n ON n.id = ne.note_id
            LEFT JOIN LATERAL (
                SELECT c.id
                FROM notes c
                JOIN note_entities ce ON ce.note_id = c.id AND ce.entity_id = ne.entity_id
                WHERE c.user_id = $1 AND c.kind = 'entity_card' AND c.status = 'active'
                ORDER BY c.id DESC
                LIMIT 1
            ) card ON true
            WHERE n.user_id = $1 AND n.status = 'active' AND n.kind <> 'entity_card'
            GROUP BY ne.entity_id, card.id
            HAVING count(*) >= $2
               AND (card.id IS NULL OR bool_or(n.id > card.id))
            ORDER BY count(*) DESC
            LIMIT $3
            """,
            user_id,
            threshold,
            limit,
        )
        return [row["entity_id"] for row in rows]

    async def notes_by_entity(
        self, user_id: UUID, entity_id: UUID, *, limit: int
    ) -> list[Note]:
        """Активные заметки сущности, свежие сверху (вход карточки)."""
        rows = await self._db.fetch(
            f"""
            SELECT {NOTE_COLUMNS_N}
            FROM notes n
            JOIN note_entities ne ON ne.note_id = n.id
            WHERE n.user_id = $1 AND ne.entity_id = $2 AND n.status = 'active'
              AND n.kind <> 'entity_card'
            ORDER BY n.id DESC
            LIMIT $3
            """,  # nosec B608 — SQL из внутренних констант, значения через $N-параметры
            user_id,
            entity_id,
            limit,
        )
        return [row_to_note(row) for row in rows]

    async def active_card_of(self, user_id: UUID, entity_id: UUID) -> Note | None:
        """Действующая карточка сущности (для supersede и связности промпта)."""
        row = await self._db.fetch_one(
            f"""
            SELECT {NOTE_COLUMNS_N}
            FROM notes n
            JOIN note_entities ne ON ne.note_id = n.id
            WHERE n.user_id = $1 AND ne.entity_id = $2
              AND n.kind = 'entity_card' AND n.status = 'active'
            ORDER BY n.id DESC
            LIMIT 1
            """,  # nosec B608 — SQL из внутренних констант, значения через $N-параметры
            user_id,
            entity_id,
        )
        return row_to_note(row) if row is not None else None

    async def observations_in_range(
        self, user_id: UUID, start: datetime, end: datetime
    ) -> list[Note]:
        """Наблюдения периода по COALESCE(event_time, observed_at), хронологически."""
        rows = await self._db.fetch(
            f"""
            SELECT {NOTE_COLUMNS}
            FROM notes
            WHERE user_id = $1 AND kind = 'observation' AND status = 'active'
              AND COALESCE(event_time, observed_at) >= $2
              AND COALESCE(event_time, observed_at) < $3
            ORDER BY COALESCE(event_time, observed_at) ASC
            """,  # nosec B608 — SQL из внутренних констант, значения через $N-параметры
            user_id,
            start,
            end,
        )
        return [row_to_note(row) for row in rows]

    async def find_summary(self, user_id: UUID, event_time: datetime) -> Note | None:
        """Сводка периода с данным началом (идемпотентность недель)."""
        row = await self._db.fetch_one(
            f"""
            SELECT {NOTE_COLUMNS}
            FROM notes
            WHERE user_id = $1 AND kind = 'period_summary' AND event_time = $2
            LIMIT 1
            """,  # nosec B608 — SQL из внутренних констант, значения через $N-параметры
            user_id,
            event_time,
        )
        return row_to_note(row) if row is not None else None

    async def find_near_duplicates(
        self,
        user_id: UUID,
        *,
        kinds: list[str],
        min_similarity: float,
        limit: int,
    ) -> list[tuple[Note, Note, float]]:
        """Пары активных почти-дублей одного kind (cosine ≥ порога).

        Self-join по embedding; a.id < b.id убирает зеркальные пары. Дизъюнктность
        пар (заметка максимум в одной паре) обеспечивает вызывающий greedy-фильтром.
        """
        a_cols = ", ".join(
            f"a.{c.strip()} AS a_{c.strip()}" for c in NOTE_COLUMNS.split(",")
        )
        b_cols = ", ".join(
            f"b.{c.strip()} AS b_{c.strip()}" for c in NOTE_COLUMNS.split(",")
        )
        rows = await self._db.fetch(
            f"""
            SELECT {a_cols}, {b_cols},
                   1 - (a.embedding <=> b.embedding) AS similarity
            FROM notes a
            JOIN notes b
              ON b.user_id = a.user_id AND b.kind = a.kind AND a.id < b.id
             AND b.status = 'active' AND b.embedding IS NOT NULL
            WHERE a.user_id = $1 AND a.status = 'active' AND a.kind = ANY($2)
              AND a.embedding IS NOT NULL
              AND 1 - (a.embedding <=> b.embedding) >= $3
            ORDER BY similarity DESC
            LIMIT $4
            """,  # nosec B608 — SQL из внутренних констант, значения через $N-параметры
            user_id,
            kinds,
            min_similarity,
            limit,
        )
        pairs: list[tuple[Note, Note, float]] = []
        for row in rows:
            left = row_to_note(row, prefix="a_")
            right = row_to_note(row, prefix="b_")
            pairs.append((left, right, float(row["similarity"])))
        return pairs

    async def recent_active_sample(
        self, user_id: UUID, *, since: datetime, limit: int
    ) -> list[Note]:
        """Случайная выборка свежих активных заметок (цели автопроб)."""
        rows = await self._db.fetch(
            f"""
            SELECT {NOTE_COLUMNS}
            FROM notes
            WHERE user_id = $1 AND status = 'active' AND observed_at >= $2
            ORDER BY random()
            LIMIT $3
            """,  # nosec B608 — SQL из внутренних констант, значения через $N-параметры
            user_id,
            since,
            limit,
        )
        return [row_to_note(row) for row in rows]

    async def statuses_of(
        self, note_ids: list[UUID], *, executor: DatabaseExecutor
    ) -> dict[UUID, str]:
        """Статусы заметок в транзакции (revalidation merge-пар перед supersede)."""
        if not note_ids:
            return {}
        rows = await executor.fetch(
            "SELECT id, status FROM notes WHERE id = ANY($1)",
            note_ids,
        )
        return {row["id"]: row["status"] for row in rows}

    async def bump_use_count(self, note_ids: list[UUID]) -> None:
        """Инкремент use_count (заметка попала в recall-блок) — сигнал демоции/ревизии."""
        if not note_ids:
            return
        await self._db.execute(
            "UPDATE notes SET use_count = use_count + 1 WHERE id = ANY($1)",
            note_ids,
        )
