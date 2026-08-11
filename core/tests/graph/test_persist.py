"""Тесты graph.persist: срез хода, санация react_loop, маппинг в WriteTurnRequest."""

from typing import Any
from unittest.mock import MagicMock
from uuid import uuid4

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
import pytest

from bestfiend.contracts.events import InputEvent
from bestfiend.graph.persist import _sanitize_react_loop, _split_turn, persist_turn
from bestfiend.memory.contracts import WriteTurnRequest


def _event() -> InputEvent:
    return InputEvent(
        user_id=uuid4(),
        message="привет",
        channel="telegram",
        request_id="req-1",
    )


def _tc(tid: str, name: str = "f") -> dict[str, Any]:
    return {"name": name, "args": {}, "id": tid, "type": "tool_call"}


def test_split_turn_strips_leading_human_and_final_ai() -> None:
    """user = ведущий Human; react_loop = середина без хвостового plain-AI."""
    turn = [
        HumanMessage(content="q"),
        AIMessage(content="", tool_calls=[_tc("c1")]),
        ToolMessage(content="r", tool_call_id="c1"),
        AIMessage(content="финал"),
    ]
    user_msg, loop = _split_turn(turn)
    assert isinstance(user_msg, HumanMessage)
    assert user_msg.content == "q"
    assert len(loop) == 2
    assert isinstance(loop[1], ToolMessage)


def test_split_turn_dialog_no_tools() -> None:
    """Диалог-ход [Human, AI(final)] → user=Human, loop пуст."""
    user_msg, loop = _split_turn([HumanMessage(content="q"), AIMessage(content="a")])
    assert isinstance(user_msg, HumanMessage)
    assert loop == []


def test_sanitize_react_loop_drops_orphan_tool_call() -> None:
    """Хвостовой AI(tool_calls) без ToolMessage снимается."""
    loop = [
        AIMessage(content="", tool_calls=[_tc("c1")]),
        ToolMessage(content="r", tool_call_id="c1"),
        AIMessage(content="", tool_calls=[_tc("c2", "g")]),
    ]
    out = _sanitize_react_loop(loop)
    assert len(out) == 2
    assert isinstance(out[-1], ToolMessage)


@pytest.mark.asyncio
async def test_persist_turn_maps_and_writes(monkeypatch) -> None:
    """turn → WriteTurnRequest: user 1 Human; loop без финала; ai = answer_text; full>loop."""
    captured: list[WriteTurnRequest] = []

    async def _write(user_id: Any, request: WriteTurnRequest, repo: Any) -> None:
        captured.append(request)

    monkeypatch.setattr("bestfiend.graph.persist.memory_write", _write)

    turn = [
        HumanMessage(content="вопрос"),
        AIMessage(content="", tool_calls=[_tc("c1")]),
        ToolMessage(content="r", tool_call_id="c1"),
        AIMessage(content="финал"),
    ]
    await persist_turn(
        _event(), turn_messages=turn, answer_text="финал", memory_runtime=MagicMock()
    )

    assert len(captured) == 1
    req = captured[0]
    assert req.request_id == "req-1"
    assert len(req.user_message) == 1
    assert req.user_message[0]["data"]["content"] == "вопрос"
    assert len(req.react_loop) == 2
    assert req.ai_message[0]["data"]["content"] == "финал"
    assert req.token_count_full > req.token_count_loop > 0


@pytest.mark.asyncio
async def test_persist_turn_sanitizes_orphan_tool_call(monkeypatch) -> None:
    """Ход кончился AI(tool_calls) без ToolMessage → react_loop пуст."""
    captured: list[WriteTurnRequest] = []

    async def _write(user_id: Any, request: WriteTurnRequest, repo: Any) -> None:
        captured.append(request)

    monkeypatch.setattr("bestfiend.graph.persist.memory_write", _write)

    turn = [HumanMessage(content="q"), AIMessage(content="", tool_calls=[_tc("c1")])]
    await persist_turn(
        _event(), turn_messages=turn, answer_text="отписка", memory_runtime=MagicMock()
    )

    assert captured[0].react_loop == []


@pytest.mark.asyncio
async def test_persist_turn_dialog_turn_empty_loop(monkeypatch) -> None:
    """Диалог-ход без тулов → react_loop пуст, ai_message = answer_text."""
    captured: list[WriteTurnRequest] = []

    async def _write(user_id: Any, request: WriteTurnRequest, repo: Any) -> None:
        captured.append(request)

    monkeypatch.setattr("bestfiend.graph.persist.memory_write", _write)

    turn = [HumanMessage(content="привет"), AIMessage(content="здравствуй")]
    await persist_turn(
        _event(),
        turn_messages=turn,
        answer_text="здравствуй",
        memory_runtime=MagicMock(),
    )

    assert captured[0].react_loop == []
    assert captured[0].ai_message[0]["data"]["content"] == "здравствуй"


@pytest.mark.asyncio
async def test_persist_turn_failsoft(monkeypatch) -> None:
    """Сбой memory_write проглочен (fail-soft фонового персиста)."""

    async def _write(*a: Any, **k: Any) -> None:
        raise RuntimeError("db down")

    monkeypatch.setattr("bestfiend.graph.persist.memory_write", _write)

    await persist_turn(
        _event(),
        turn_messages=[HumanMessage(content="q"), AIMessage(content="a")],
        answer_text="a",
        memory_runtime=MagicMock(),
    )
