"""react-нода: один шаг ReAct-цикла исполнителя.

Зовёт LLM (`bind_tools`: каталог + опц. `delegate_subtask` для рекурсии), на
первой итерации инициализирует `work_history` (System + Human). Роутинг:
tool_calls → tools; plain text → финальный ответ в `result` → END; soft-gate по
`remaining_steps` — для subagent summarize-and-return, для top-level → error.
"""

from collections.abc import Callable
from typing import Any, cast

from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    HumanMessage,
    SystemMessage,
)
from langchain_core.runnables import Runnable
from langgraph.graph import END
from langgraph.runtime import Runtime
from langgraph.types import Command
from loguru import logger

from bestfiend.graph.context import GraphContext
from bestfiend.graph.nodes.message_utils import message_text
from bestfiend.graph.state import ErrorSignal, OrchestrationState
from bestfiend.graph.stream_keys import (
    ANSWER_DELTA_KEY,
    ANSWER_RESET_KEY,
    PROGRESS_STEP_KEY,
)

from .delegate_tool import DELEGATE_SUBTASK_NAME, DELEGATE_SUBTASK_TOOL
from .prompts import render_react_runtime, render_react_system
from .send_artifact_tool import (
    SEND_ARTIFACT_TO_USER_NAME,
    SEND_ARTIFACT_TO_USER_TOOL,
)
from .summarize import summarize_progress


_INTERNAL_TOOL_NAMES: frozenset[str] = frozenset(
    {DELEGATE_SUBTASK_NAME, SEND_ARTIFACT_TO_USER_NAME}
)


def _prepend_runtime_context(message: HumanMessage, runtime_ctx: str) -> HumanMessage:
    """Эфемерная копия Human с волатильным контекстом перед текстом юзера.

    Списочный контент (гидрированные image-блоки) сохраняется: контекст
    вклеивается в первый text-блок, картинки не теряются.
    """
    content = message.content
    if isinstance(content, str):
        return HumanMessage(content=f"{runtime_ctx}\n\n{content}")
    blocks = list(content)
    for i, block in enumerate(blocks):
        if isinstance(block, dict) and block.get("type") == "text":
            blocks[i] = {**block, "text": f"{runtime_ctx}\n\n{block.get('text', '')}"}
            break
    else:
        blocks.insert(0, {"type": "text", "text": runtime_ctx})
    return HumanMessage(content=blocks)


async def react_node(
    state: OrchestrationState,
    runtime: Runtime[GraphContext],
) -> Command:
    """Один шаг ReAct: зовёт LLM с тулами; роутит в tools / финал (result) / soft-gate."""
    ctx = runtime.context
    if state.remaining_steps <= ctx.soft_gate_limit:
        return await _on_soft_gate(state, runtime)

    prepend, history = _prepare_react_history(state)
    messages: list[BaseMessage] = [
        SystemMessage(content=render_react_system(state)),
        *history,
    ]
    tools = _available_react_tools(state, ctx)
    bound = ctx.model.bind_tools(tools)
    ai = await _invoke_react_model(state, runtime, bound, messages, len(tools))
    return _route_react_result(state, prepend, ai)


def _prepare_react_history(
    state: OrchestrationState,
) -> tuple[list[BaseMessage], list[BaseMessage]]:
    """Готовит локальную историю вызова без изменения STM."""
    prepend: list[BaseMessage] = []
    history: list[BaseMessage] = list(state.active_history)
    if not history:
        # Пустая лента — субагент на первом шаге: кладём под-задачу. Top-level
        # стартует с непустым stm (Human уже там) и сюда не попадает.
        prepend = [HumanMessage(content=state.task_for_react)]
        history = prepend
    elif not state.is_subagent:
        # Top-level: эфемерно обогащаем Human текущего turn'а волатильным
        # контекстом (время + память). Правка только в локальной копии history —
        # stm и Command(update) хранят чистый Human (кешируемость префикса +
        # чистый persist). content — str или список блоков (гидрированные картинки).
        runtime_ctx = render_react_runtime(state)
        if runtime_ctx:
            idx = state.turn_start_index
            current = history[idx] if 0 <= idx < len(history) else None
            if isinstance(current, HumanMessage):
                history[idx] = _prepend_runtime_context(current, runtime_ctx)
    return prepend, history


def _available_react_tools(state: OrchestrationState, ctx: GraphContext) -> list[Any]:
    """Возвращает доступные текущему уровню ReAct-инструменты."""
    tools = [
        tool
        for name, tool in ctx.tools_by_name.items()
        if not (state.is_subagent and name in ctx.top_level_only_tool_names)
    ]
    if state.recursion_depth < ctx.max_recursion_depth:
        tools.append(DELEGATE_SUBTASK_TOOL)
    if not state.is_subagent:
        tools.append(SEND_ARTIFACT_TO_USER_TOOL)
    return tools


async def _invoke_react_model(
    state: OrchestrationState,
    runtime: Runtime[GraphContext],
    bound: Runnable[Any, BaseMessage],
    messages: list[BaseMessage],
    tool_count: int,
) -> AIMessage:
    """Вызывает модель и логирует границы ReAct-шага."""
    logger.debug(
        "react: invoke model request_id={} mode={} tools={} history_len={} depth={}",
        state.input.request_id,
        state.processing_mode,
        tool_count,
        len(messages) - 1,
        state.recursion_depth,
    )
    try:
        if state.is_subagent:
            ai = await bound.ainvoke(messages)
        else:
            ai = await _stream_react(bound, messages, runtime)
    except Exception as exc:
        logger.warning(
            "react: model call failed request_id={} {}: {}",
            state.input.request_id,
            type(exc).__name__,
            exc,
        )
        raise
    ai_message = cast(AIMessage, ai)
    logger.debug(
        "react: model returned request_id={} tool_calls={} text_len={}",
        state.input.request_id,
        len(ai_message.tool_calls),
        len(message_text(ai_message)),
    )
    return ai_message


def _route_react_result(
    state: OrchestrationState,
    prepend: list[BaseMessage],
    ai: AIMessage,
) -> Command:
    """Маршрутизирует ответ модели в tools или завершение уровня."""
    to_add: list[BaseMessage] = [*prepend, ai]
    field = state.active_history_field
    if ai.tool_calls:
        return Command(update={field: to_add}, goto="tools")
    return Command(
        update={field: to_add, "result": message_text(ai)},
        goto=END,
    )


async def _on_soft_gate(
    state: OrchestrationState,
    runtime: Runtime[GraphContext],
) -> Command:
    """Бюджет исчерпан: subagent сворачивает работу в `result`; top-level → error."""
    if state.processing_mode == "subagent":
        summary = await summarize_progress(state, runtime)
        return Command(update={"result": summary}, goto=END)
    return Command(
        update={
            "error_signal": ErrorSignal(
                kind="loop_exhausted",
                node="react",
                message=(
                    f"remaining_steps={state.remaining_steps} "
                    f"<= {runtime.context.soft_gate_limit}"
                ),
            )
        },
        goto="error",
    )


async def _stream_react(
    bound: Runnable[Any, BaseMessage],
    messages: list[BaseMessage],
    runtime: Runtime[GraphContext],
) -> AIMessage:
    """Стримит LLM-шаг с классификацией preface vs финальный ответ.

    content стримим оптимистично как AnswerDelta (живой draft). Если шаг свернул
    в tool_calls (первый `tool_call_chunk`) — стримленный content был preface:
    эмитим его одной ProgressStep + ANSWER_RESET (consumer обнуляет накопитель
    ответа, preface не попадает в финал). content без tool_calls остаётся
    финальным ответом. Для каждого нового `index` с непустым `name` (после фильтра
    внутренних) — одна ProgressStep «вызываю {name}» (parallel tool_calls + защита
    от duplicate args-only чанков). Обрыв стрима после видимых дельт → ANSWER_RESET
    перед re-raise: retry стримит с чистого накопителя (без дубля), error-нода
    не дописывает к обрубку.
    """
    writer = runtime.stream_writer
    accumulated: AIMessageChunk | None = None
    tool_started = False
    emitted_indices: set[int] = set()
    streamed_since_reset = False

    try:
        async for raw_chunk in bound.astream(messages):
            chunk = cast(AIMessageChunk, raw_chunk)
            accumulated = chunk if accumulated is None else accumulated + chunk
            tool_started, streamed_since_reset = _process_answer_chunk(
                chunk,
                accumulated,
                writer,
                tool_started=tool_started,
                streamed_since_reset=streamed_since_reset,
            )
            _emit_tool_progress(chunk, emitted_indices, writer)
    except Exception:
        if streamed_since_reset:
            writer({ANSWER_RESET_KEY: ""})
        raise

    # AIMessageChunk — subclass BaseMessage; add_messages reducer и роутинг
    # по .tool_calls работают без явной конверсии.
    return cast(AIMessage, accumulated)


def _process_answer_chunk(
    chunk: AIMessageChunk,
    accumulated: AIMessageChunk,
    writer: Callable[[dict[str, str]], Any],
    *,
    tool_started: bool,
    streamed_since_reset: bool,
) -> tuple[bool, bool]:
    """Публикует текст чанка и сбрасывает preface перед tool calls."""
    if tool_started:
        return tool_started, streamed_since_reset
    text = message_text(chunk)
    if text:
        writer({ANSWER_DELTA_KEY: text})
        streamed_since_reset = True
    if chunk.tool_call_chunks:
        preface = message_text(accumulated)
        if preface:
            writer({PROGRESS_STEP_KEY: preface})
        writer({ANSWER_RESET_KEY: ""})
        return True, False
    return False, streamed_since_reset


def _emit_tool_progress(
    chunk: AIMessageChunk,
    emitted_indices: set[int],
    writer: Callable[[dict[str, str]], Any],
) -> None:
    """Публикует один progress-шаг на внешний tool call."""
    for tool_call_chunk in chunk.tool_call_chunks or ():
        index = tool_call_chunk.get("index")
        name = tool_call_chunk.get("name")
        if index is None or index in emitted_indices or not name:
            continue
        emitted_indices.add(index)
        if name not in _INTERNAL_TOOL_NAMES:
            writer({PROGRESS_STEP_KEY: f"вызываю {name}"})
