"""Доменные модели storage MCP-подключений (зеркала строк)."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from bestfiend.contracts.mcp import McpAuthType, McpTransport
from bestfiend.control_plane.mcp.oauth.models import (
    McpOAuthClientRecord,
    McpOAuthStatus,
)


class McpConnectionRecord(BaseModel):
    """Строка mcp_connections — определение MCP-сервера, один на всех."""

    model_config = ConfigDict(extra="forbid")

    connection_id: UUID
    name: str
    url: str
    transport: McpTransport
    auth_type: McpAuthType
    is_public: bool
    is_system: bool
    timeout_s: float
    supports_parallel_tool_calls: bool = (
        True  # false → граф сериализует вызовы к серверу
    )
    created_at: datetime
    updated_at: datetime | None = None


class McpSubscriptionRecord(BaseModel):
    """Строка mcp_subscriptions — подписка юзера на подключение."""

    model_config = ConfigDict(extra="forbid")

    user_id: UUID
    connection_id: UUID
    auth_token: str | None = None
    enabled: bool
    disabled_tools: list[str] = Field(default_factory=list)
    timeout_s: float | None = None  # персональный оверрайд; None = дефолт сервера
    created_at: datetime


class McpServerWithSubscription(BaseModel):
    """Вью для UI my-servers: connection-дефолты + subscription-оверрайды раздельно.

    В отличие от ResolvedMcpServer (COALESCE-эффективные значения для графа), держит
    дефолт сервера и персональный оверрайд порознь — UI показывает оба. `sub_*` = None
    когда подписки нет; маркер наличия — `has_subscription`, НЕ auth_token (он легитимно
    None у none-auth подписки).
    """

    model_config = ConfigDict(extra="forbid")

    connection_id: UUID
    name: str
    url: str
    transport: McpTransport
    auth_type: McpAuthType
    is_public: bool
    is_system: bool
    timeout_s: float  # дефолт сервера (connection.timeout_s)
    has_subscription: bool
    sub_enabled: bool | None
    sub_auth_token: str | None
    sub_disabled_tools: list[str] | None
    sub_timeout_s: float | None
    sub_created_at: datetime | None
    oauth_status: McpOAuthStatus | None = None  # None для не-oauth серверов


class McpConnectionWithOAuthClient(BaseModel):
    """Admin-вью подключения с состоянием OAuth-клиента (источник для McpConnectionView).

    `oauth_client` = None для не-oauth подключений и для oauth без записи клиента (DCR
    ещё не выполнялся). client_secret наружу не отдаётся — маппинг в view его отбрасывает.
    """

    model_config = ConfigDict(extra="forbid")

    connection: McpConnectionRecord
    oauth_client: McpOAuthClientRecord | None = None
