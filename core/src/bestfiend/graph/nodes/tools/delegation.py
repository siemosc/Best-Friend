"""Запуск дочернего графа через инструмент делегирования."""

from typing import Any

from langchain_core.messages import BaseMessage, ToolCall, ToolMessage
from langgraph.runtime import Runtime

from bestfiend.contracts.artifacts import ArtifactRef
from bestfiend.graph.attached_artifacts import enrich_human_with_artifacts
from bestfiend.graph.context import GraphContext
from bestfiend.graph.nodes.tools.artifacts import (
    coerce_artifact_refs,
    known_artifacts,
)
from bestfiend.graph.state import OrchestrationState
from bestfiend.mcp.coercion import render_created_artifacts_md


_EMPTY_SUBTASK_RESULT = "(под-задача не дала результата)"


async def run_delegated_subtask(
    state: OrchestrationState,
    runtime: Runtime[GraphContext],
    tool_call: ToolCall,
) -> ToolMessage:
    """Запускает дочерний граф и возвращает его результат инструменту."""
    tool_call_id = tool_call["id"] or ""
    graph = runtime.context.graph
    if graph is None:
        return ToolMessage(
            content="Error: рекурсия недоступна — граф не прокинут в context",
            tool_call_id=tool_call_id,
            status="error",
        )
    task = str(tool_call["args"].get("task", ""))
    child_payload, missing_names = await _prepare_child_payload(
        state, runtime, tool_call, task
    )
    try:
        output = await graph.ainvoke(
            child_payload,
            context=runtime.context,
            config={"recursion_limit": runtime.context.child_recursion_limit},
        )
    except Exception as exc:  # noqa: BLE001 — сбой под-задачи изолируем
        return ToolMessage(
            content=f"Error: под-задачу решить не удалось: {exc}",
            tool_call_id=tool_call_id,
            status="error",
        )
    return _delegate_result_message(output, missing_names, tool_call_id)


async def _prepare_child_payload(
    state: OrchestrationState,
    runtime: Runtime[GraphContext],
    tool_call: ToolCall,
    task: str,
) -> tuple[dict[str, Any], list[str]]:
    """Готовит состояние дочернего графа и список отсутствующих файлов."""
    child_payload: dict[str, Any] = {
        "input": state.input.model_copy(update={"message": task}),
        "processing_mode": "subagent",
        "recursion_depth": state.recursion_depth + 1,
    }
    requested_names = [
        str(name) for name in (tool_call["args"].get("artifact_llm_names") or [])
    ]
    refs, missing_names = _resolve_requested_artifacts(state, requested_names)
    if refs:
        child_payload["work_history"] = await _build_child_history(runtime, task, refs)
    return child_payload, missing_names


def _resolve_requested_artifacts(
    state: OrchestrationState,
    requested_names: list[str],
) -> tuple[list[ArtifactRef], list[str]]:
    requested = set(requested_names)
    refs = [ref for ref in known_artifacts(state) if ref.artifact_llm_name in requested]
    resolved = {ref.artifact_llm_name for ref in refs}
    missing = [name for name in requested_names if name not in resolved]
    return refs, missing


async def _build_child_history(
    runtime: Runtime[GraphContext],
    task: str,
    refs: list[ArtifactRef],
) -> list[BaseMessage]:
    history: list[BaseMessage] = [enrich_human_with_artifacts(task, refs)]
    if runtime.context.hydrate_images is not None:
        return await runtime.context.hydrate_images(history)
    return history


def _delegate_result_message(
    output: dict[str, Any],
    missing_names: list[str],
    tool_call_id: str,
) -> ToolMessage:
    result = str(output.get("result", "")) or _EMPTY_SUBTASK_RESULT
    child_created = coerce_artifact_refs(output.get("created_artifacts"))
    content = result
    if child_created:
        block = render_created_artifacts_md(child_created)
        content = f"{result}\n\n{block}\n(можно отдать юзеру)"
    if missing_names:
        content += (
            "\n\n(Запрошенные файлы не найдены и не передавались воркеру: "
            f"{', '.join(missing_names)})"
        )
    return ToolMessage(
        content=content,
        tool_call_id=tool_call_id,
        artifact=child_created or None,
    )
