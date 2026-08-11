"""Тесты резки ленты по бюджету (_select_by_budget) и сборки в load_log_tail."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    ToolMessage,
    messages_to_dict,
)
import pytest

from bestfiend.memory.settings import MemorySettings
from bestfiend.memory.turns.contracts import Turn
from bestfiend.memory.turns.tail import _select_by_budget, load_log_tail


def _make_turn(*, token_count_full: int, turn_id: int) -> Turn:
    return Turn(
        id=turn_id,
        user_id=uuid4(),
        request_id=f"req-{turn_id}",
        user_message=messages_to_dict([HumanMessage(content=f"q{turn_id}")]),
        react_loop=[],
        ai_message=messages_to_dict([AIMessage(content=f"a{turn_id}")]),
        token_count_full=token_count_full,
        token_count_loop=0,
        created_at=datetime(2026, 5, 2, 10, 0, 0, tzinfo=UTC),
    )


class TestSelectByBudget:
    """Резка целыми ходами по token_count_full (вход newest-first)."""

    def test_all_fit(self) -> None:
        turns = [_make_turn(token_count_full=100, turn_id=i) for i in range(5)]
        assert len(_select_by_budget(turns, 1000)) == 5

    def test_exact_budget(self) -> None:
        turns = [_make_turn(token_count_full=100, turn_id=i) for i in range(5)]
        assert len(_select_by_budget(turns, 500)) == 5

    def test_cuts_oldest(self) -> None:
        """turns newest-first (id0 — самый свежий); бюджет на 3 хода."""
        turns = [_make_turn(token_count_full=100, turn_id=i) for i in range(10)]
        result = _select_by_budget(turns, 300)
        assert [t.id for t in result] == [0, 1, 2]

    def test_single_turn_over_budget_kept(self) -> None:
        """Один ход больше бюджета — всё равно остаётся (самый свежий)."""
        turns = [_make_turn(token_count_full=500, turn_id=0)]
        assert len(_select_by_budget(turns, 100)) == 1

    def test_empty(self) -> None:
        assert _select_by_budget([], 1000) == []


@pytest.mark.asyncio
async def test_load_log_tail_assembles_full_turns_and_appends_current() -> None:
    """Лента: ходы в хронологии (user+loop+ai) + текущий Human последним; tool-пары целы."""
    user_id = uuid4()
    loop = messages_to_dict(
        [
            AIMessage(
                content="",
                tool_calls=[{"name": "f", "args": {}, "id": "c1", "type": "tool_call"}],
            ),
            ToolMessage(content="r", tool_call_id="c1"),
        ]
    )
    turn_new = Turn(
        id=2,
        user_id=user_id,
        request_id="req-2",
        user_message=messages_to_dict([HumanMessage(content="q2")]),
        react_loop=loop,
        ai_message=messages_to_dict([AIMessage(content="a2")]),
        token_count_full=10,
        token_count_loop=5,
        created_at=datetime(2026, 5, 2, 11, 0, 0, tzinfo=UTC),
    )
    turn_old = Turn(
        id=1,
        user_id=user_id,
        request_id="req-1",
        user_message=messages_to_dict([HumanMessage(content="q1")]),
        react_loop=[],
        ai_message=messages_to_dict([AIMessage(content="a1")]),
        token_count_full=10,
        token_count_loop=0,
        created_at=datetime(2026, 5, 2, 10, 0, 0, tzinfo=UTC),
    )
    repo = AsyncMock()
    repo.recent_turns.return_value = [turn_new, turn_old]  # newest-first

    messages = await load_log_tail(user_id, repo, MemorySettings(), "сейчас", 30_000)

    first = messages[0]
    last = messages[-1]
    assert isinstance(first, HumanMessage) and first.content == "q1"
    assert isinstance(last, HumanMessage) and last.content == "сейчас"
    ai_idx = next(
        i for i, m in enumerate(messages) if isinstance(m, AIMessage) and m.tool_calls
    )
    answer = messages[ai_idx + 1]
    assert isinstance(answer, ToolMessage)
    assert answer.tool_call_id == "c1"
