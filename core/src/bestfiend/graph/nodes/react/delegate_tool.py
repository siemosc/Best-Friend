"""Routing-only тула delegate_subtask: делегирует под-задачу дочернему react-прогону.

Модель зовёт её, чтобы отколоть самодостаточную под-проблему. Исполняется НЕ
через свой coroutine, а специально в tools-ноде (нужен доступ к графу и state
для само-рекурсии). Coroutine — защитная заглушка.
"""

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from bestfiend.graph.nodes.react.routing_only_tool import unreachable_tool_callback


DELEGATE_SUBTASK_NAME = "delegate_subtask"


class _DelegateArgs(BaseModel):
    """Аргументы delegate_subtask."""

    task: str = Field(
        description=(
            "A self-contained subtask: spell out pronouns and references, add the "
            "needed context, name files and entities explicitly. The worker does "
            "not see your history. In the user's language."
        ),
    )
    artifact_llm_names: list[str] = Field(
        default_factory=list,
        description=(
            "Files the worker should receive alongside the task (e.g. images to "
            "analyze): exact names from the «Приложенные файлы» block or from tool "
            "results. The worker gets their content natively."
        ),
    )


DELEGATE_SUBTASK_TOOL = StructuredTool.from_function(
    coroutine=unreachable_tool_callback(DELEGATE_SUBTASK_NAME),
    name=DELEGATE_SUBTASK_NAME,
    description=(
        "Hand off a substantial, self-contained part of the work to a separate "
        "worker and get back a finished result — a distilled summary, not the raw "
        "work. Use it when a part of the task is heavy on its own: a long series "
        "of steps, deep search, or working through a large body of material — the "
        "worker does it separately and returns the outcome, while your context "
        "stays clean for assembling the overall answer. It also fits when there "
        "are several independent parts that can run at once. DO NOT delegate "
        "small, pinpoint actions or steps that depend on your unfinished work — "
        "do those yourself."
    ),
    args_schema=_DelegateArgs,
)
