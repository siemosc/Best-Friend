"""Ops-лог памяти (core.memory_ops): провенанс каждой операции над заметками.

Записи делаются в транзакции вызывающего (executor-aware) — лог атомарен
с самой операцией: нет операции без следа и следа без операции.
"""

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from bestfiend.memory.db import DatabaseExecutor, MemoryDatabaseClient


OpsPipeline = Literal["observer", "reconciler", "reflector", "tool", "sleep", "ui"]
OpsAction = Literal[
    "add",
    "supersede",
    "noop",
    "contradict",
    "evict",
    "reflect",
    "pin",
    "unpin",
    "demote",
    "revise",
    "merge",
    "delete",
    "edit",
]

# Потолок detail: контекст решения, не дамп контента.
_DETAIL_MAX_CHARS = 200

_INSERT_OP_SQL = """
INSERT INTO memory_ops (user_id, pipeline, op, note_id, target_note_id, detail)
VALUES ($1, $2, $3, $4, $5, $6)
"""


@dataclass(frozen=True, slots=True)
class MemoryOperation:
    """Одна операция для записи в лог."""

    pipeline: OpsPipeline
    op: OpsAction
    note_id: UUID | None = None
    target_note_id: UUID | None = None
    detail: str | None = None


class MemoryOperationLogRepository:
    """Запись операций памяти в core.memory_ops."""

    __slots__ = ("_db",)

    def __init__(self, db: MemoryDatabaseClient) -> None:
        self._db = db

    async def log(
        self,
        user_id: UUID,
        ops: list[MemoryOperation],
        *,
        executor: DatabaseExecutor | None = None,
    ) -> None:
        """Пишет батч операций; с executor — в транзакцию вызывающего."""
        target = executor or self._db
        for op in ops:
            await target.execute(
                _INSERT_OP_SQL,
                user_id,
                op.pipeline,
                op.op,
                op.note_id,
                op.target_note_id,
                _clip_detail(op.detail),
            )


def _clip_detail(detail: str | None) -> str | None:
    """Обрезает detail до потолка (лог — контекст, не хранилище контента)."""
    if detail is None or len(detail) <= _DETAIL_MAX_CHARS:
        return detail
    return detail[:_DETAIL_MAX_CHARS] + "…"
