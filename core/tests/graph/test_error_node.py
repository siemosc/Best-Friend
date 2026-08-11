"""Тесты error-ноды: static / finalize / инжект запроса / fallback в static."""

from typing import Any, Literal

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langgraph.graph import END
from langgraph.runtime import Runtime
import pytest

from bestfiend.graph.context import GraphContext
from bestfiend.graph.nodes.error.messages import STATIC_TEXTS
from bestfiend.graph.nodes.error.node import _finalize_messages, error_node
from bestfiend.graph.state import (
    ErrorSignal,
    InputContext,
    OrchestrationState,
    RenderedPrompts,
)
from tests.graph.fakes import bindable_chat_model, raising_model


def _state(
    kind: str,
    history: list[BaseMessage] | None = None,
    *,
    processing_mode: Literal["task", "subagent"] = "task",
) -> OrchestrationState:
    """State для error-ноды: история turn'а в активной ленте (stm для task)."""
    lane: list[BaseMessage] = history or []
    sub = processing_mode == "subagent"
    return OrchestrationState(
        input=InputContext(message="вопрос пользователя", request_id="r1"),
        prompts=RenderedPrompts(environment="ENV", user_instruction="ANSWER"),
        processing_mode=processing_mode,
        turn_start_index=0,
        error_signal=ErrorSignal(kind=kind, node="react", message="boom"),  # type: ignore[arg-type]
        stm=[] if sub else lane,
        work_history=lane if sub else [],
    )


@pytest.mark.asyncio
async def test_error_static_provider_down() -> None:
    """provider_down → static-фраза, без LLM, goto END."""
    captured: list[Any] = []
    cmd = await error_node(
        _state("provider_down"),
        Runtime(
            context=GraphContext(model=bindable_chat_model([])),
            stream_writer=captured.append,
        ),
    )

    assert cmd.goto == END
    text = "".join(c["answer_delta"] for c in captured)
    assert text == STATIC_TEXTS["provider_down"]


@pytest.mark.asyncio
async def test_error_finalize_streams_from_history() -> None:
    """context_exceeded → finalize: answer-модель стримит по собранному, goto END."""
    captured: list[Any] = []
    history = [
        HumanMessage(content="вопрос пользователя"),
        AIMessage(content="кое-что собрал"),
    ]
    model = bindable_chat_model([AIMessage(content="частичный ответ")])
    cmd = await error_node(
        _state("context_exceeded", history),
        Runtime(
            context=GraphContext(model=model),
            stream_writer=captured.append,
        ),
    )

    assert cmd.goto == END
    text = "".join(c["answer_delta"] for c in captured)
    assert text == "частичный ответ"


@pytest.mark.asyncio
async def test_error_finalize_failure_falls_back_to_static() -> None:
    """Сбой finalize-LLM → откат в static (goto END, не падает)."""
    captured: list[Any] = []
    model = raising_model(RuntimeError("answer model dead"))
    cmd = await error_node(
        _state("context_exceeded", []),
        Runtime(
            context=GraphContext(model=model),
            stream_writer=captured.append,
        ),
    )

    assert cmd.goto == END
    text = "".join(c["answer_delta"] for c in captured)
    assert (
        text == STATIC_TEXTS["unexpected"]
    )  # context_exceeded нет в STATIC_TEXTS → дефолт


def test_finalize_messages_injects_request_when_history_empty() -> None:
    """Пустой work_history → инжектим HumanMessage(input.message), иначе запрос потерян."""
    msgs = _finalize_messages(_state("loop_exhausted", []), "loop_exhausted")

    assert isinstance(msgs[0], SystemMessage)
    assert isinstance(msgs[1], HumanMessage)
    assert msgs[1].content == "вопрос пользователя"


def test_finalize_messages_keeps_existing_human() -> None:
    """В транскрипте уже есть HumanMessage → второй не инжектим."""
    history = [
        HumanMessage(content="найди погоду"),
        AIMessage(content="искал"),
    ]
    msgs = _finalize_messages(_state("context_exceeded", history), "context_exceeded")

    humans = [m for m in msgs if isinstance(m, HumanMessage)]
    assert len(humans) == 1
    assert humans[0].content == "найди погоду"


@pytest.mark.asyncio
async def test_error_subagent_writes_result_without_stream() -> None:
    """subagent: итог в `result`, без стрима (уходит родителю как ToolMessage)."""
    captured: list[Any] = []
    state = OrchestrationState(
        input=InputContext(message="под-задача", request_id="r1"),
        prompts=RenderedPrompts(environment="ENV", user_instruction="ANSWER"),
        processing_mode="subagent",
        error_signal=ErrorSignal(kind="provider_down", node="react", message="boom"),
    )

    cmd = await error_node(
        state,
        Runtime(
            context=GraphContext(model=bindable_chat_model([])),
            stream_writer=captured.append,
        ),
    )

    assert cmd.goto == END
    assert cmd.update is not None
    assert cmd.update["result"] == STATIC_TEXTS["provider_down"]
    assert captured == []  # subagent не стримит пользователю
