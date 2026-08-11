"""Тестовые двойники журнала ходов памяти."""

import asyncio
from datetime import UTC, datetime
from uuid import UUID, uuid4

from bestfiend.memory.turns.contracts import Turn


def make_turn(turn_id: int, *, tokens: int = 100, user_text: str = "q") -> Turn:
    """Создаёт ход лога с заданным id и количеством токенов."""
    return Turn(
        id=turn_id,
        user_id=uuid4(),
        request_id=f"req-{turn_id}",
        user_message=[{"type": "human", "data": {"content": user_text}}],
        react_loop=[],
        ai_message=[{"type": "ai", "data": {"content": "a"}}],
        token_count_full=tokens,
        token_count_loop=0,
        created_at=datetime(2026, 6, 9, 12, 0, tzinfo=UTC),
    )


class TurnRepositoryFake:
    """Журнал с фиксированными ходами и настраиваемой задержкой."""

    def __init__(self, turns: list[Turn], *, delay_s: float = 0.0) -> None:
        self.turns = turns
        self.delay_s = delay_s
        self.token_sum_calls = 0

    async def unprocessed_token_sum(self, user_id: UUID, after_id: int) -> int:
        self.token_sum_calls += 1
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        return sum(turn.token_count_full for turn in self.turns if turn.id > after_id)

    async def turns_after(
        self,
        user_id: UUID,
        after_id: int,
        limit: int,
    ) -> list[Turn]:
        return [turn for turn in self.turns if turn.id > after_id][:limit]
