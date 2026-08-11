"""Pydantic-контракты HTTP API MCP-management.

Request/response для connections (admin-CRUD), subscriptions (per-user) и
discover-preview (test-connection). Все request-модели — `extra="forbid"`.
Зеркало доменных моделей `control_plane/mcp/models.py`, но без чувствительных
для UI инвариантов (токен чужой подписки не отдаём — см. router-слой).
"""

from datetime import datetime
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from bestfiend.control_plane.mcp.models import McpAuthType, McpTransport
from bestfiend.control_plane.mcp.oauth.models import (
    McpOAuthClientSource,
    McpOAuthStatus,
)


_NAME_MAX_LEN = 64  # = mcp_connections.name varchar(64)
_URL_MAX_LEN = 500  # = mcp_connections.url varchar(500)
_TIMEOUT_MIN = 1.0
_TIMEOUT_MAX = 300.0
_TOKEN_MAX_LEN = 4096  # auth_token = text; кап на вход
_TOOL_NAME_MAX_LEN = 128
_DISABLED_TOOLS_MAX = 256
_OAUTH_CLIENT_ID_MAX_LEN = 512  # client_id = text; кап на вход
_OAUTH_CLIENT_SECRET_MAX_LEN = 512  # client_secret = text; кап на вход
_URL_SCHEMES = ("http://", "https://")


def _validate_http_url(value: str) -> str:
    """Минимальный SSRF-барьер: пускаем только http(s)-схему (не file://, gopher://)."""
    if not value.startswith(_URL_SCHEMES):
        raise ValueError("url must start with http:// or https://")
    return value


class OAuthStartResponse(BaseModel):
    """Ответ POST .../oauth/start: authorization URL для редиректа браузера юзера."""

    model_config = ConfigDict(extra="forbid")

    authorization_url: str


class McpConnectionView(BaseModel):
    """Исходящая модель подключения (admin-CRUD) — зеркало McpConnectionRecord."""

    model_config = ConfigDict(extra="forbid")

    connection_id: UUID
    name: str
    url: str
    transport: McpTransport
    auth_type: McpAuthType
    is_public: bool
    is_system: bool
    timeout_s: float
    supports_parallel_tool_calls: bool
    created_at: datetime
    updated_at: datetime | None
    # OAuth-клиент подключения (None для не-oauth и oauth без записи клиента).
    # client_secret наружу не отдаётся — маппинг в router его отбрасывает.
    oauth_client_id: str | None = None
    oauth_client_source: McpOAuthClientSource | None = None


class CreateMcpConnectionRequest(BaseModel):
    """Тело POST /mcp/connections (admin). is_system не принимается — всегда false."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=_NAME_MAX_LEN)
    url: str = Field(min_length=1, max_length=_URL_MAX_LEN)
    transport: McpTransport = "http_stream"
    auth_type: McpAuthType = "none"
    is_public: bool = False
    timeout_s: float = Field(default=30.0, ge=_TIMEOUT_MIN, le=_TIMEOUT_MAX)
    supports_parallel_tool_calls: bool = True
    # Предрегистрированные OAuth-креды (Google-сценарий); пустые = DCR.
    oauth_client_id: str | None = Field(
        default=None, max_length=_OAUTH_CLIENT_ID_MAX_LEN
    )
    oauth_client_secret: str | None = Field(
        default=None, max_length=_OAUTH_CLIENT_SECRET_MAX_LEN
    )

    @field_validator("url")
    @classmethod
    def _check_url(cls, value: str) -> str:
        return _validate_http_url(value)


class UpdateMcpConnectionRequest(BaseModel):
    """Тело PATCH /mcp/connections/{id} (admin) — partial, без is_system."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=_NAME_MAX_LEN)
    url: str | None = Field(default=None, min_length=1, max_length=_URL_MAX_LEN)
    transport: McpTransport | None = None
    auth_type: McpAuthType | None = None
    is_public: bool | None = None
    timeout_s: float | None = Field(default=None, ge=_TIMEOUT_MIN, le=_TIMEOUT_MAX)
    supports_parallel_tool_calls: bool | None = None
    # Патч-семантика: unset → OAuth-клиент не трогаем (см. router exclude_unset).
    oauth_client_id: str | None = Field(
        default=None, max_length=_OAUTH_CLIENT_ID_MAX_LEN
    )
    oauth_client_secret: str | None = Field(
        default=None, max_length=_OAUTH_CLIENT_SECRET_MAX_LEN
    )

    @field_validator("url")
    @classmethod
    def _check_url(cls, value: str | None) -> str | None:
        return _validate_http_url(value) if value is not None else value


class SubscriptionView(BaseModel):
    """Персональный оверрайд-блок подписки (auth_token отдаётся только владельцу)."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool
    auth_token: str | None
    disabled_tools: list[str]
    timeout_s: float | None  # None = нет персонального оверрайда (дефолт сервера)
    created_at: datetime


class McpServerSubscriptionView(BaseModel):
    """Элемент GET /mcp/my-servers: сервер + моя подписка (или null для public без неё)."""

    model_config = ConfigDict(extra="forbid")

    connection_id: UUID
    name: str
    url: str
    transport: McpTransport
    auth_type: McpAuthType
    is_public: bool
    is_system: bool
    timeout_s: float  # дефолт сервера
    subscription: SubscriptionView | None
    oauth_status: McpOAuthStatus | None = None  # None для не-oauth серверов


class UpsertSubscriptionRequest(BaseModel):
    """Тело PUT /mcp/subscriptions/{id} (session) — полная замена состояния подписки."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    auth_token: str | None = Field(default=None, max_length=_TOKEN_MAX_LEN)
    disabled_tools: list[str] = Field(
        default_factory=list, max_length=_DISABLED_TOOLS_MAX
    )
    timeout_s: float | None = Field(default=None, ge=_TIMEOUT_MIN, le=_TIMEOUT_MAX)

    @field_validator("disabled_tools")
    @classmethod
    def _check_tool_names(cls, value: list[str]) -> list[str]:
        for name in value:
            if not name or len(name) > _TOOL_NAME_MAX_LEN:
                raise ValueError(
                    f"disabled_tools entries must be 1..{_TOOL_NAME_MAX_LEN} chars"
                )
        return value


class DiscoverPreviewRequest(BaseModel):
    """Тело POST /mcp/discover-preview: либо connection_id (by-id), либо ad-hoc url."""

    model_config = ConfigDict(extra="forbid")

    connection_id: UUID | None = None
    url: str | None = Field(default=None, max_length=_URL_MAX_LEN)
    auth_type: McpAuthType | None = None
    auth_token: str | None = Field(default=None, max_length=_TOKEN_MAX_LEN)

    @field_validator("url")
    @classmethod
    def _check_url(cls, value: str | None) -> str | None:
        return _validate_http_url(value) if value is not None else value

    @model_validator(mode="after")
    def _exactly_one_form(self) -> Self:
        adhoc = any(x is not None for x in (self.url, self.auth_type, self.auth_token))
        if self.connection_id is not None and adhoc:
            raise ValueError(
                "provide either connection_id OR ad-hoc url fields, not both"
            )
        if self.connection_id is None and self.url is None:
            raise ValueError("provide connection_id or url")
        return self


class DiscoveredToolView(BaseModel):
    """Тул сервера в preview (name + description; input_schema не отдаём)."""

    model_config = ConfigDict(extra="forbid")

    name: str
    description: str


class DiscoverPreviewFailureView(BaseModel):
    """Причина неудачного preview (graceful, HTTP остаётся 200)."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["timeout", "auth", "unreachable", "protocol"]
    message: str


class DiscoverPreviewResponse(BaseModel):
    """Ответ preview: instructions + тулзы, либо failure (фиктивный ad-hoc id не светим)."""

    model_config = ConfigDict(extra="forbid")

    connection_id: UUID | None
    name: str
    instructions: str | None
    tools: list[DiscoveredToolView]
    failure: DiscoverPreviewFailureView | None
