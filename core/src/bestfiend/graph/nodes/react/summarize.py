"""summarize_progress: свернуть проделанную работу в самодостаточный итог.

react зовёт для subagent на soft-gate (бюджет шагов исчерпан): вместо ухода в
error — один work-вызов по `work_history`, итог уходит родителю как результат
под-задачи.
"""

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langgraph.runtime import Runtime

from bestfiend.graph.context import GraphContext
from bestfiend.graph.nodes.message_utils import message_text
from bestfiend.graph.nodes.react.prompts import SUMMARIZE_NUDGE, render_react_system
from bestfiend.graph.state import OrchestrationState


async def summarize_progress(
    state: OrchestrationState, runtime: Runtime[GraphContext]
) -> str:
    """Один work-вызов: сворачивает `work_history` в самодостаточный итог-текст."""
    messages: list[BaseMessage] = [
        SystemMessage(content=render_react_system(state)),
        *state.work_history,
        HumanMessage(content=SUMMARIZE_NUDGE),
    ]
    ai = await runtime.context.model.ainvoke(messages)
    return message_text(ai)
