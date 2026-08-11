"""Доменные модели OAuth-тракта MCP (зеркала строк таблиц mcp_oauth_*)."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict


McpOAuthClientSource = Literal["preregistered", "dcr"]
McpOAuthStatus = Literal["not_connected", "connected", "expired"]


class McpOAuthClientRecord(BaseModel):
    """Строка mcp_oauth_clients — OAuth-клиент per connection."""

    model_config = ConfigDict(extra="forbid")

    connection_id: UUID
    client_id: str
    client_secret: str | None = None  # NULL для public-клиентов (DCR без секрета)
    # Метод клиентской аутентификации; сервис валидирует значение до записи,
    # поэтому здесь сырой text-зеркал колонки, не Literal.
    token_endpoint_auth_method: str
    source: McpOAuthClientSource
    client_secret_expires_at: datetime | None = None
    created_at: datetime
    updated_at: datetime | None = None


class McpOAuthFlowRecord(BaseModel):
    """Строка mcp_oauth_flows — незавершённая авторизация, одноразовая."""

    model_config = ConfigDict(extra="forbid")

    state: str
    user_id: UUID
    connection_id: UUID
    code_verifier: str
    redirect_uri: str
    token_endpoint: str  # зафиксирован на start, callback не переоткрывает discovery
    issuer: str  # ожидаемый AS issuer для сверки `iss` из callback (RFC 9207)
    resource: str  # RFC 8707, одинаковый в обоих запросах
    scope: str | None = None
    expires_at: datetime
    created_at: datetime


class McpOAuthTokenRecord(BaseModel):
    """Строка mcp_oauth_tokens — токены per (user, connection)."""

    model_config = ConfigDict(extra="forbid")

    user_id: UUID
    connection_id: UUID
    access_token: str
    refresh_token: str | None = None
    expires_at: datetime | None = None  # NULL = AS не сообщил срок
    scope: str | None = None
    token_endpoint: str  # для refresh без re-discovery
    refresh_failed_at: datetime | None = None  # отказ refresh → статус expired
    created_at: datetime
    updated_at: datetime | None = None
