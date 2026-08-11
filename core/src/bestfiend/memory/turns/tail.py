"""Хвост лога — загрузка ленты ходов, резка по бюджету, сборка сообщений."""

from uuid import UUID

from langchain_core.messages import BaseMessage, HumanMessage, messages_from_dict

from bestfiend.memory.settings import MemorySettings
from bestfiend.memory.turns.contracts import Turn
from bestfiend.memory.turns.repository import TurnRepository


async def load_log_tail(
    user_id: UUID,
    repository: TurnRepository,
    settings: MemorySettings,
    current_message: str,
    tail_budget: int,
) -> list[BaseMessage]:
    """Грузит последние ходы, режет целыми ходами по бюджету, собирает ленту + текущий Human."""
    turns = await repository.recent_turns(user_id, settings.log_max_fetch_turns)
    kept = _select_by_budget(turns, tail_budget)
    kept.reverse()  # newest-first → хронология

    messages: list[BaseMessage] = []
    for turn in kept:
        messages.extend(messages_from_dict(turn.user_message))
        messages.extend(messages_from_dict(turn.react_loop))
        messages.extend(messages_from_dict(turn.ai_message))
    messages.append(HumanMessage(content=current_message))
    return messages


def _select_by_budget(turns_newest_first: list[Turn], budget: int) -> list[Turn]:
    """Оставляет ходы newest→older пока сумма token_count_full ≤ budget; самый свежий — всегда."""
    kept: list[Turn] = []
    total = 0
    for i, turn in enumerate(turns_newest_first):
        total += turn.token_count_full
        if i > 0 and total > budget:
            break
        kept.append(turn)
    return kept
