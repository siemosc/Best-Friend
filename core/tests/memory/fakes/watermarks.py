"""Тестовые двойники watermark-репозитория памяти."""

from typing import Any
from uuid import UUID

from tests.memory.fakes.database import is_committed_operation_visible


class WatermarkRepositoryFake:
    """Watermark-репозиторий со staged advance и настраиваемыми сбоями."""

    def __init__(self) -> None:
        self.staged: list[tuple[Any, str, int]] = []
        self.advance_executors: list[Any] = []
        self.fail_advances_left = 0

    @property
    def positions(self) -> dict[str, int]:
        """Возвращает позиции, видимые после commit."""
        result: dict[str, int] = {}
        for executor, pipeline, turn_id in self.staged:
            if is_committed_operation_visible(executor):
                result[pipeline] = max(result.get(pipeline, 0), turn_id)
        return result

    async def get(self, user_id: UUID, pipeline: str) -> int:
        return self.positions.get(pipeline, 0)

    async def advance(
        self,
        user_id: UUID,
        pipeline: str,
        last_turn_id: int,
        *,
        executor: Any = None,
    ) -> None:
        self.advance_executors.append(executor)
        if self.fail_advances_left > 0:
            self.fail_advances_left -= 1
            raise RuntimeError("advance failed (simulated)")
        self.staged.append((executor, pipeline, last_turn_id))
