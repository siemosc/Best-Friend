"""End-to-end граф: react ⇄ tools, plain-text финал, рекурсия (delegate_subtask), error."""

from typing import Any

import pytest

from bestfiend.graph import GraphContext, build_graph
from bestfiend.graph.nodes.react.delegate_tool import DELEGATE_SUBTASK_NAME
from bestfiend.graph.state import InputContext
from tests.graph.fakes import (
    ScriptedToolCall,
    ScriptedTurn,
    echo_tool,
    raise_first_astream_then_stream_model,
    raising_model,
    scripted_streaming_model,
)


class _ContextError(Exception):
    """Имитация ошибки превышения контекста провайдера (400 + паттерн)."""

    status_code = 400

    def __init__(self) -> None:
        super().__init__("maximum context length exceeded")


@pytest.mark.asyncio
async def test_full_flow_react_tools_then_plain_text() -> None:
    """react зовёт search, затем отвечает текстом → `result`; граф доходит до END."""
    work = scripted_streaming_model(
        ScriptedTurn(
            content="ищу погоду",
            tool_calls=[
                ScriptedToolCall(name="search", args='{"q":"погода"}', id="c1")
            ],
        ),
        ScriptedTurn(content="В Москве +15°C"),
    )
    ctx = GraphContext(
        model=work,
        tools_by_name={"search": echo_tool("search", "+15°C")},
    )
    inp = InputContext(message="найди погоду", request_id="r1")

    payload: dict[str, Any] = {"input": inp, "processing_mode": "task"}
    out = await build_graph().ainvoke(payload, context=ctx)  # type: ignore[arg-type]

    assert out["result"] == "В Москве +15°C"
    roles = [type(m).__name__ for m in out["stm"]]
    assert roles[0] == "HumanMessage"  # task в начале ленты; System не хранится
    assert "ToolMessage" in roles  # search исполнен
    assert "SystemMessage" not in roles  # System prepend на invoke, не в ленте
    assert any(getattr(m, "content", "") == "+15°C" for m in out["stm"])


@pytest.mark.asyncio
async def test_recursion_delegate_subtask_returns_to_parent() -> None:
    """react делегирует под-задачу → дочерний react решает → результат вернулся → финал."""
    work = scripted_streaming_model(
        # 1) parent (top-level, astream) делегирует под-задачу
        ScriptedTurn(
            content="делю задачу",
            tool_calls=[
                ScriptedToolCall(
                    name=DELEGATE_SUBTASK_NAME, args='{"task":"часть A"}', id="d1"
                )
            ],
        ),
        # 2) child react (subagent, ainvoke через _generate) отвечает текстом — это его result
        ScriptedTurn(content="часть A готова"),
        # 3) parent (top-level, astream) финализирует
        ScriptedTurn(content="итог: часть A готова"),
    )
    ctx = GraphContext(graph=build_graph(), model=work)
    inp = InputContext(message="большая задача", request_id="r1")

    payload: dict[str, Any] = {"input": inp, "processing_mode": "task"}
    out = await build_graph().ainvoke(payload, context=ctx)  # type: ignore[arg-type]

    assert out["result"] == "итог: часть A готова"
    tool_msgs = [m for m in out["stm"] if type(m).__name__ == "ToolMessage"]
    assert any("часть A готова" in m.content for m in tool_msgs)


# Примечание: error-путь проверяем через `graph.ainvoke` (не `astream(stream_mode="custom")`):
# в langgraph 1.2.1 при live-стриминге (custom/messages) исходное исключение может
# дойти до consumer'а ДОПОЛНИТЕЛЬНО к работе error_handler. Routing error_handler →
# error-нода корректен на invoke/values/updates; live-стрим на escape гасится в
# GraphRuntime outer try.


@pytest.mark.asyncio
async def test_error_path_unexpected_routes_to_error_node() -> None:
    """Нода падает (unexpected) → error_handler классифицирует → error-нода → END."""
    work = raising_model(ValueError("boom"))
    ctx = GraphContext(model=work)
    inp = InputContext(message="сделай X", request_id="r1")

    payload: dict[str, Any] = {"input": inp, "processing_mode": "task"}
    out = await build_graph().ainvoke(payload, context=ctx)  # type: ignore[arg-type]

    assert out["error_signal"].kind == "unexpected"
    assert out["error_signal"].node == "react"


@pytest.mark.asyncio
async def test_error_path_context_exceeded_routes_to_finalize() -> None:
    """Нода падает (context_exceeded) → error-нода finalize той же моделью, END без escape."""
    # Одна модель: первый astream (react top-level) кидает context-ошибку,
    # второй astream (error_node finalize) отвечает.
    model = raise_first_astream_then_stream_model(
        _ContextError(), "ответ по собранному"
    )
    ctx = GraphContext(model=model)
    inp = InputContext(message="сделай X", request_id="r1")

    payload: dict[str, Any] = {"input": inp, "processing_mode": "task"}
    out = await build_graph().ainvoke(payload, context=ctx)  # type: ignore[arg-type]

    assert out["error_signal"].kind == "context_exceeded"
    assert out["result"] == "ответ по собранному"
