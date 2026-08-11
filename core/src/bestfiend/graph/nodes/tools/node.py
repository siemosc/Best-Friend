"""tools-нода: исполняет батч tool_calls react'а; собирает артефакты за turn.

Своя async-нода (не `ToolNode`): per-call изоляция (сбой одного тула →
error-`ToolMessage`, не рушит батч). `delegate_subtask` запускает дочерний
react-прогон (само-рекурсия); `send_artifact_to_user` резолвит выбранные артефакты
в `presented_artifacts`. Созданные работниками рефы едут в `ToolMessage.artifact` →
накапливаются в `created_artifacts`.
"""

import asyncio
from typing import Any

from langchain_core.messages import AIMessage, ToolCall, ToolMessage
from langgraph.runtime import Runtime
from langgraph.types import Command

from bestfiend.graph.context import GraphContext
from bestfiend.graph.nodes.react.delegate_tool import DELEGATE_SUBTASK_NAME
from bestfiend.graph.nodes.react.send_artifact_tool import SEND_ARTIFACT_TO_USER_NAME
from bestfiend.graph.nodes.tools.artifacts import (
    collect_created_artifacts,
    known_artifacts,
    resolve_presented_artifacts,
)
from bestfiend.graph.nodes.tools.delegation import run_delegated_subtask
from bestfiend.graph.state import OrchestrationState


async def tools_node(
    state: OrchestrationState,
    runtime: Runtime[GraphContext],
) -> Command:
    """Исполняет tool_calls последнего AIMessage; копит created/presented артефакты.

    Вызовы к серверам без параллельности (`serial_tool_servers`) сериализуются
    семафором=1 per-server; остальные и не-MCP-тулы исполняются параллельно.
    """
    last = state.active_history[-1]
    tool_calls = last.tool_calls if isinstance(last, AIMessage) else []
    serial_servers = runtime.context.serial_tool_servers
    semaphores: dict[str, asyncio.Semaphore] = {}

    async def _run(tool_call: ToolCall) -> ToolMessage:
        server = serial_servers.get(tool_call["name"])
        if server is None:  # параллельный сервер или не-MCP-тул
            return await _dispatch(state, runtime, tool_call)
        sem = semaphores.get(server)
        if sem is None:
            sem = asyncio.Semaphore(1)
            semaphores[server] = sem
        async with sem:  # один вызов к этому серверу за раз
            return await _dispatch(state, runtime, tool_call)

    results = await asyncio.gather(*(_run(tc) for tc in tool_calls))
    update: dict[str, Any] = {state.active_history_field: list(results)}
    created = collect_created_artifacts(results)
    if created:
        update["created_artifacts"] = created
    presented = resolve_presented_artifacts(state, tool_calls)
    if presented:
        update["presented_artifacts"] = presented
    return Command(update=update, goto="react")


async def _dispatch(
    state: OrchestrationState,
    runtime: Runtime[GraphContext],
    tool_call: ToolCall,
) -> ToolMessage:
    """Роутит tool_call: delegate → граф, send_artifact → ветка, иначе обычный тул."""
    name = tool_call["name"]
    if name == DELEGATE_SUBTASK_NAME:
        return await run_delegated_subtask(state, runtime, tool_call)
    if name == SEND_ARTIFACT_TO_USER_NAME:
        return _run_send_artifact(state, tool_call)
    return await _run_one(runtime.context, tool_call)


async def _run_one(ctx: GraphContext, tool_call: ToolCall) -> ToolMessage:
    """Исполняет один tool_call; любой сбой изолирован в error-ToolMessage."""
    tc_id = tool_call["id"] or ""
    tool = ctx.tools_by_name.get(tool_call["name"])
    if tool is None:
        return ToolMessage(
            content=f"Error: unknown tool '{tool_call['name']}'",
            tool_call_id=tc_id,
            status="error",
        )
    try:
        result = await tool.ainvoke(tool_call)
    except Exception as exc:  # noqa: BLE001 — сбой тула изолируем в ToolMessage
        return ToolMessage(content=f"Error: {exc}", tool_call_id=tc_id, status="error")
    if isinstance(result, ToolMessage):
        return result
    return ToolMessage(content=str(result), tool_call_id=tc_id)


def _run_send_artifact(
    state: OrchestrationState,
    tool_call: ToolCall,
) -> ToolMessage:
    """Фидбэк модели; запись в presented делает resolve_presented_artifacts."""
    tc_id = tool_call["id"] or ""
    names = [str(n) for n in (tool_call["args"].get("artifact_llm_names") or [])]
    known = {ref.artifact_llm_name for ref in known_artifacts(state)}
    matched, missing = _partition_artifact_names(names, known)
    if not matched:
        return _missing_artifacts_message(names, known, tc_id)
    return _attached_artifacts_message(matched, missing, tc_id)


def _partition_artifact_names(
    names: list[str], known: set[str]
) -> tuple[list[str], list[str]]:
    """Разделяет запрошенные имена на найденные и отсутствующие."""
    return (
        [name for name in names if name in known],
        [name for name in names if name not in known],
    )


def _missing_artifacts_message(
    names: list[str], known: set[str], tool_call_id: str
) -> ToolMessage:
    """Возвращает ошибку при полном отсутствии запрошенных файлов."""
    available = ", ".join(sorted(known)) or "—"
    return ToolMessage(
        content=(
            f"Ни один артефакт не найден среди доступных: {', '.join(names)}. "
            f"Доступные: {available}."
        ),
        tool_call_id=tool_call_id,
        status="error",
    )


def _attached_artifacts_message(
    matched: list[str], missing: list[str], tool_call_id: str
) -> ToolMessage:
    """Возвращает подтверждение прикрепления найденных файлов."""
    note = f"Прикреплены к ответу: {', '.join(matched)}."
    if missing:
        note += f" Не найдены: {', '.join(missing)}."
    return ToolMessage(content=note, tool_call_id=tool_call_id)
