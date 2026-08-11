"""Тесты общих хелперов сообщений: history_for_answer + message_text."""

from typing import Any

from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

from bestfiend.graph.nodes.message_utils import history_for_answer, message_text


def _tail_tc() -> dict[str, Any]:
    return {"name": "search", "args": {"q": "x"}, "id": "c1", "type": "tool_call"}


def _search_tc() -> dict[str, Any]:
    return {"name": "search", "args": {"q": "погода"}, "id": "c0", "type": "tool_call"}


def test_history_for_answer_trims_tail_toolcall_and_systems() -> None:
    """Все system выкидываются, хвостовой AIMessage с tool_calls отрезается."""
    history = [
        SystemMessage(content="SYS"),
        HumanMessage(content="task"),
        AIMessage(content="ищу", tool_calls=[_search_tc()]),
        ToolMessage(content="found", tool_call_id="c0"),
        AIMessage(content="", tool_calls=[_tail_tc()]),
    ]

    out = history_for_answer(history)

    assert [type(m).__name__ for m in out] == [
        "HumanMessage",
        "AIMessage",
        "ToolMessage",
    ]
    assert out[-1].content == "found"


def test_history_for_answer_keeps_plain_tail() -> None:
    """Закрытый хвостовой AIMessage (текст без tool_calls) не режем."""
    history = [
        SystemMessage(content="SYS"),
        HumanMessage(content="task"),
        AIMessage(content="готово"),
    ]

    out = history_for_answer(history)

    assert [type(m).__name__ for m in out] == ["HumanMessage", "AIMessage"]
    assert out[-1].content == "готово"


def test_message_text_str_and_blocks() -> None:
    """message_text: str-контент как есть, list-блоки — склейка text-частей."""
    assert message_text(AIMessageChunk(content="привет")) == "привет"
    assert message_text(AIMessage(content="полный")) == "полный"

    blocks = AIMessageChunk(
        content=[{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]
    )
    assert message_text(blocks) == "ab"
