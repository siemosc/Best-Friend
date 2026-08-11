"""error-нода: единственный путь «ответить хоть чем-то» при сбое.

По `error_signal.kind`: provider_down/unexpected → static-отписка (LLM не
трогаем); context_exceeded/loop_exhausted → finalize (один LLM-вызов по
собранному, на сбое внутри → static). Итог всегда в `result`. Для `subagent`
НЕ стримим — результат уходит родителю как ToolMessage, не пользователю.
Без `error_handler` (иначе рекурсия; её краш ловит outer net в GraphRuntime).
"""

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langgraph.graph import END
from langgraph.runtime import Runtime
from langgraph.types import Command
from loguru import logger

from bestfiend.graph.context import GraphContext
from bestfiend.graph.nodes.message_utils import history_for_answer, message_text
from bestfiend.graph.state import OrchestrationState, RenderedPrompts
from bestfiend.graph.stream_keys import ANSWER_DELTA_KEY

from .messages import FINALIZE_RULES, KIND_HINTS, STATIC_TEXTS


_FINALIZE_KINDS = ("context_exceeded", "loop_exhausted")


async def error_node(
    state: OrchestrationState,
    runtime: Runtime[GraphContext],
) -> Command:
    """Отвечает static или finalize. Итог в `result`; для subagent без стрима."""
    signal = state.error_signal
    kind = signal.kind if signal else "unexpected"
    is_subagent = state.processing_mode == "subagent"

    if kind in _FINALIZE_KINDS:
        try:
            text = await _finalize(state, runtime, kind, stream=not is_subagent)
            return Command(update={"result": text}, goto=END)
        except Exception as exc:  # noqa: BLE001 — finalize ненадёжен → откат в static
            logger.warning(
                "error: finalize failed request_id={} kind={}: {}",
                state.input.request_id,
                kind,
                exc,
            )

    static = STATIC_TEXTS.get(kind, STATIC_TEXTS["unexpected"])
    if not is_subagent:
        runtime.stream_writer({ANSWER_DELTA_KEY: static})
    logger.debug("error: static request_id={} kind={}", state.input.request_id, kind)
    return Command(update={"result": static}, goto=END)


async def _finalize(
    state: OrchestrationState,
    runtime: Runtime[GraphContext],
    kind: str,
    *,
    stream: bool,
) -> str:
    """Один LLM-вызов (слот `answer`) по собранному; копит текст, опц. стримит дельты."""
    ctx = runtime.context
    writer = runtime.stream_writer
    messages = _finalize_messages(state, kind)
    parts: list[str] = []
    async for chunk in ctx.model.astream(messages):
        delta = message_text(chunk)
        if delta:
            parts.append(delta)
            if stream:
                writer({ANSWER_DELTA_KEY: delta})
    return "".join(parts)


def _finalize_messages(state: OrchestrationState, kind: str) -> list[BaseMessage]:
    """System (FINALIZE_RULES + хинт) + транскрипт; при пустой истории — инжект запроса."""
    history = history_for_answer(state.turn_history)
    if not any(isinstance(m, HumanMessage) for m in history):
        # История turn'а пуста (react упал на первом вызове) — без запроса
        # finalize-модель не знает, на что отвечать.
        request = state.task_for_react or state.input.message
        history = [HumanMessage(content=request), *history]
    return [SystemMessage(content=_system_prompt(state.prompts, kind)), *history]


def _system_prompt(prompts: RenderedPrompts, kind: str) -> str:
    """Окружение/память/answer-инструкция + FINALIZE_RULES + хинт по виду сбоя."""
    parts = [
        prompts.environment,
        prompts.memory_stable,
        prompts.memory_recall,
        prompts.user_instruction,
        FINALIZE_RULES,
        KIND_HINTS.get(kind, ""),
    ]
    return "\n\n".join(part for part in parts if part)
