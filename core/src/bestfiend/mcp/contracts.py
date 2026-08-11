"""Runtime-DTO MCP discovery: каталог тулзов + результат опроса сервера."""

from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID


@dataclass(slots=True)
class ToolInfo:
    """Нормализованный tool из MCP `list_tools` (name + description + input schema)."""

    name: str
    description: str
    input_schema: dict[str, Any]


DiscoveryFailureKind = Literal["timeout", "auth", "unreachable", "protocol"]


@dataclass(slots=True)
class DiscoveryFailure:
    """Причина, по которой опрос сервера не удался."""

    kind: DiscoveryFailureKind
    message: str


@dataclass(slots=True)
class ServerDiscovery:
    """Результат опроса одного MCP-сервера: успех (tools+instructions) или фейл."""

    connection_id: UUID
    name: str
    instructions: str | None
    tools: list[ToolInfo]
    failure: DiscoveryFailure | None = None
