"""Контракты слоя лога."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID


@dataclass(frozen=True, slots=True)
class Turn:
    """Один сохранённый ход — одна строка core.turns.

    Сообщения лежат как messages_to_dict (1-в-1 langchain BaseMessage):
    `user_message` — [Human], `react_loop` — [AI(tool_calls), Tool, ...] (пустой без тулов),
    `ai_message` — [AI] (доставленное на UI). Резка целыми ходами по `token_count_full`.
    """

    id: int
    user_id: UUID
    request_id: str
    user_message: list[dict[str, Any]]
    react_loop: list[dict[str, Any]]
    ai_message: list[dict[str, Any]]
    token_count_full: int
    token_count_loop: int
    created_at: datetime
