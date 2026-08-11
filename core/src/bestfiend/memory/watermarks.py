"""Watermarks — позиции фоновых пайплайнов в логе (идемпотентность прогонов)."""

from uuid import UUID

from bestfiend.memory.db import DatabaseExecutor, MemoryDatabaseClient


OBSERVER_PIPELINE = "observer"


class WatermarkRepository:
    """Доступ к core.memory_watermarks: последний обработанный ход per (user, pipeline)."""

    __slots__ = ("_db",)

    def __init__(self, db: MemoryDatabaseClient) -> None:
        self._db = db

    async def get(self, user_id: UUID, pipeline: str) -> int:
        """Последний обработанный turn id; 0 если пайплайн ещё не работал."""
        row = await self._db.fetch_one(
            "SELECT last_turn_id FROM memory_watermarks WHERE user_id = $1 AND pipeline = $2",
            user_id,
            pipeline,
        )
        return int(row["last_turn_id"]) if row is not None else 0

    async def advance(
        self,
        user_id: UUID,
        pipeline: str,
        last_turn_id: int,
        *,
        executor: DatabaseExecutor | None = None,
    ) -> None:
        """Двигает watermark вперёд; назад не откатывает (GREATEST)."""
        await (executor or self._db).execute(
            """
            INSERT INTO memory_watermarks (user_id, pipeline, last_turn_id, updated_at)
            VALUES ($1, $2, $3, now())
            ON CONFLICT (user_id, pipeline) DO UPDATE
            SET last_turn_id = GREATEST(memory_watermarks.last_turn_id, EXCLUDED.last_turn_id),
                updated_at = now()
            """,
            user_id,
            pipeline,
            last_turn_id,
        )
