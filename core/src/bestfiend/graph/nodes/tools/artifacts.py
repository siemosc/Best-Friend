"""Сбор и разрешение артефактов для tools-ноды."""

from typing import Any

from langchain_core.messages import ToolCall, ToolMessage

from bestfiend.contracts.artifacts import ArtifactRef
from bestfiend.graph.nodes.react.send_artifact_tool import SEND_ARTIFACT_TO_USER_NAME
from bestfiend.graph.state import OrchestrationState


def collect_created_artifacts(results: list[ToolMessage]) -> list[ArtifactRef]:
    """Собирает созданные артефакты из результатов батча инструментов."""
    refs: list[ArtifactRef] = []
    for message in results:
        for ref in getattr(message, "artifact", None) or []:
            if isinstance(ref, ArtifactRef):
                refs.append(ref)
    return refs


def known_artifacts(state: OrchestrationState) -> list[ArtifactRef]:
    """Возвращает доступные модели приложенные и созданные артефакты."""
    known: dict[str, ArtifactRef] = {}
    for ref in [*state.input.attached_artifacts, *state.created_artifacts]:
        known.setdefault(ref.artifact_id, ref)
    return list(known.values())


def resolve_presented_artifacts(
    state: OrchestrationState,
    tool_calls: list[ToolCall],
) -> list[ArtifactRef]:
    """Разрешает артефакты, выбранные для отправки пользователю."""
    if state.is_subagent:
        return []
    requested: set[str] = set()
    for tool_call in tool_calls:
        if tool_call["name"] == SEND_ARTIFACT_TO_USER_NAME:
            requested.update(
                str(name)
                for name in (tool_call["args"].get("artifact_llm_names") or [])
            )
    if not requested:
        return []
    return [ref for ref in known_artifacts(state) if ref.artifact_llm_name in requested]


def coerce_artifact_refs(raw: Any) -> list[ArtifactRef]:
    """Нормализует словари и ArtifactRef в единый список ссылок."""
    return [
        item if isinstance(item, ArtifactRef) else ArtifactRef.model_validate(item)
        for item in raw or []
    ]
