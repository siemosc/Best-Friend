"""Коэрция результата MCP-тула в (content, artifacts) для ToolMessage.

Контрактный диспетчер по структуре результата, не по имени сервера: результат,
несущий наш контракт (`result` + `artifacts`), рендерится нашим путём (текст для
модели + полные ArtifactRef машинно в artifact-канал); прочее — серверный текст.
Fail-soft: кривой/частичный контракт деградирует в generic.
"""

from typing import Any

from fastmcp.client.client import CallToolResult
from mcp.types import TextContent
import orjson
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from bestfiend.contracts.artifacts import ArtifactRef


class McpToolPayload(BaseModel):
    """Контракт результата artifact-aware MCP-тула (любой сервер может его вернуть)."""

    model_config = ConfigDict(extra="ignore")

    result: str = ""
    artifacts: list[ArtifactRef] = Field(default_factory=list)


def coerce_tool_result(result: CallToolResult) -> tuple[str, list[ArtifactRef] | None]:
    """Приводит CallToolResult к (content, artifacts) для ToolMessage (диспетчер по контракту)."""
    if result.is_error:
        return _error_text(result), None
    payload = _try_payload(result.structured_content)
    if payload is not None and payload.artifacts:
        return _render_with_artifacts(payload), payload.artifacts
    return _generic_text(result), None


def _try_payload(structured: dict[str, Any] | None) -> McpToolPayload | None:
    """Парсит structured_content в наш контракт; None/кривой → None (→ generic путь)."""
    if not structured:
        return None
    try:
        return McpToolPayload.model_validate(structured)
    except ValidationError:
        return None


def _render_with_artifacts(payload: McpToolPayload) -> str:
    """Текст для модели: result + MD-блок созданных артефактов (полные refs едут машинно)."""
    base = payload.result or "Готово."
    return f"{base}\n\n{render_created_artifacts_md(payload.artifacts)}"


def render_created_artifacts_md(refs: list[ArtifactRef]) -> str:
    """MD-блок созданных артефактов (egress): заголовок + bullets `имя — описание`."""
    if not refs:
        return ""
    bullets = "\n".join(_artifact_bullet(ref) for ref in refs)
    return f"Созданные артефакты:\n{bullets}"


def _artifact_bullet(ref: ArtifactRef) -> str:
    """Один bullet созданного артефакта: `artifact_llm_name` + ` — описание`, если оно есть."""
    line = f"- `{ref.artifact_llm_name}`"
    if ref.description:
        line += f" — {ref.description}"
    return line


def _generic_text(result: CallToolResult) -> str:
    """Серверный текст: склейка text-блоков .content; если пусто — JSON structured_content."""
    texts = [block.text for block in result.content if isinstance(block, TextContent)]
    if texts:
        return "\n".join(texts)
    if result.structured_content is not None:
        return orjson.dumps(result.structured_content).decode("utf-8")
    return ""


def _error_text(result: CallToolResult) -> str:
    """Текст ошибки тула (is_error) для модели."""
    body = _generic_text(result) or "tool returned an error"
    return f"Ошибка тула: {body}"
