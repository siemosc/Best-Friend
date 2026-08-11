"""Нейтральный кросс-модульный контракт MCP-сервера.

`ResolvedMcpServer` производит control_plane (резолв connection+subscription),
потребляют mcp (клиент/discovery) и graph (tool_builder) — чистый pass-through
дескриптор без вызова владеющей capability.
"""

from typing import Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict


McpTransport = Literal["http_stream"]  # задел: +"stdio" (L4)
McpAuthType = Literal["none", "bearer", "oauth"]


class ResolvedMcpServer(BaseModel):
    """Эффективное состояние сервера для юзера (вью резолва list_for_user).

    Собирается из JOIN mcp_connections с mcp_subscriptions: auth_token и
    disabled_tools берутся из подписки (для public без подписки — None и []).
    """

    model_config = ConfigDict(extra="forbid")

    connection_id: UUID
    name: str
    url: str
    transport: McpTransport
    auth_type: McpAuthType
    timeout_s: float
    is_public: bool
    auth_token: str | None
    disabled_tools: list[str]
    supports_parallel_tool_calls: bool = (
        True  # false → сериализация вызовов в tools-ноде
    )


class McpServerResolver(Protocol):
    """Порт резолва доступных юзеру MCP-серверов (сторона graph).

    Реализацию (McpSubscriptionRepository) владеет control_plane; graph получает
    её инжектом и не знает про storage-детали. При сбое реализация бросает свою
    доменную ошибку — вызывающий деградирует fail-soft (граф без тулов).
    """

    async def list_for_user(self, user_id: UUID) -> list[ResolvedMcpServer]:
        """Возвращает эффективный список серверов юзера (public ∪ подписки)."""
        ...
