"""Хранилище результатов автоматических проб recall."""

from uuid import UUID

from bestfiend.memory.db import MemoryDatabaseClient


class ProbeRepository:
    """Записывает результаты проб в ``core.memory_probes``."""

    __slots__ = ("_db",)

    def __init__(self, db: MemoryDatabaseClient) -> None:
        self._db = db

    async def record(
        self,
        user_id: UUID,
        *,
        question: str,
        expected_note_id: UUID,
        hit: bool,
        rank: int | None,
    ) -> None:
        """Записывает вопрос, ожидаемую заметку, попадание и позицию."""
        await self._db.execute(
            """
            INSERT INTO memory_probes (user_id, question, expected_note_id, hit, rank)
            VALUES ($1, $2, $3, $4, $5)
            """,
            user_id,
            question,
            expected_note_id,
            hit,
            rank,
        )
