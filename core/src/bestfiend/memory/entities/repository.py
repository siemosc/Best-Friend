"""PostgreSQL repository для реестра сущностей (core.entities + core.entity_aliases)."""

from uuid import UUID

import asyncpg
from loguru import logger
from uuid6 import uuid7

from bestfiend.memory.db import MemoryDatabaseClient
from bestfiend.memory.entities.contracts import Entity


# Алиасы короче 3 символов в текстовом матче дают ложные срабатывания (предлоги,
# местоимения) — отсекаем на запросе, не на записи.
_MIN_ALIAS_MATCH_LEN = 3


class EntityRepository:
    """Реестр сущностей: каноничные имена + алиасы, резолв упоминаний."""

    __slots__ = ("_db",)

    def __init__(self, db: MemoryDatabaseClient) -> None:
        self._db = db

    async def list_entities(self, user_id: UUID) -> list[Entity]:
        """Все сущности пользователя с алиасами (для промпта Observer)."""
        rows = await self._db.fetch(
            """
            SELECT e.id, e.user_id, e.canonical_name,
                   COALESCE(array_agg(a.alias) FILTER (WHERE a.alias IS NOT NULL), '{}') AS aliases
            FROM entities e
            LEFT JOIN entity_aliases a ON a.entity_id = e.id
            WHERE e.user_id = $1
            GROUP BY e.id, e.user_id, e.canonical_name
            ORDER BY e.created_at ASC
            """,
            user_id,
        )
        return [
            Entity(
                id=row["id"],
                user_id=row["user_id"],
                canonical_name=row["canonical_name"],
                aliases=tuple(row["aliases"]),
            )
            for row in rows
        ]

    async def resolve_names(self, user_id: UUID, names: list[str]) -> dict[str, UUID]:
        """Резолвит имена в entity_id точным lower-матчем по каноничному имени или алиасу."""
        if not names:
            return {}
        rows = await self._db.fetch(
            """
            SELECT lower(n.name) AS lookup, e.id
            FROM unnest($2::text[]) AS n(name)
            JOIN entities e
              ON e.user_id = $1
             AND (lower(e.canonical_name) = lower(n.name)
                  OR EXISTS (
                        SELECT 1 FROM entity_aliases a
                        WHERE a.entity_id = e.id AND lower(a.alias) = lower(n.name)
                  ))
            """,
            user_id,
            names,
        )
        by_lookup = {row["lookup"]: row["id"] for row in rows}
        return {
            name: by_lookup[name.lower()] for name in names if name.lower() in by_lookup
        }

    async def create_entity(self, user_id: UUID, canonical_name: str) -> UUID:
        """Создаёт сущность с алиасом = имени; гонку по уникальному имени разрешает повторным резолвом."""
        entity_id = uuid7()
        try:
            async with self._db.transaction() as tx:
                await tx.execute(
                    "INSERT INTO entities (id, user_id, canonical_name) VALUES ($1, $2, $3)",
                    entity_id,
                    user_id,
                    canonical_name,
                )
                await tx.execute(
                    "INSERT INTO entity_aliases (entity_id, alias) VALUES ($1, $2)",
                    entity_id,
                    canonical_name,
                )
        except asyncpg.UniqueViolationError:
            # Параллельный прогон успел создать ту же сущность — берём существующую.
            resolved = await self.resolve_names(user_id, [canonical_name])
            existing = resolved.get(canonical_name)
            if existing is None:
                logger.warning(
                    "EntityRepository: unique race без резолва name={}",
                    canonical_name,
                )
                raise
            return existing
        return entity_id

    async def canonical_name_of(self, entity_id: UUID) -> str | None:
        """Каноничное имя сущности (заголовок карточки)."""
        row = await self._db.fetch_one(
            "SELECT canonical_name FROM entities WHERE id = $1",
            entity_id,
        )
        return row["canonical_name"] if row is not None else None

    async def match_in_text(self, user_id: UUID, text: str) -> list[UUID]:
        """Сущности, чьи алиасы встречаются в тексте (case-insensitive подстрока)."""
        rows = await self._db.fetch(
            """
            SELECT DISTINCT e.id
            FROM entities e
            JOIN entity_aliases a ON a.entity_id = e.id
            WHERE e.user_id = $1
              AND length(a.alias) >= $3
              AND position(lower(a.alias) IN lower($2)) > 0
            """,
            user_id,
            text,
            _MIN_ALIAS_MATCH_LEN,
        )
        return [row["id"] for row in rows]
