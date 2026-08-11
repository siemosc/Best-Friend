"""PostgreSQL repositories для MCP-storage: connections + subscriptions.

McpConnectionRepository — CRUD определений серверов (admin-managed).
McpSubscriptionRepository — CRUD подписок user<->connection + резолв доступа
(`list_for_user`): public ∪ private-с-подпиской, минус выключенные.
"""

from typing import Any
from uuid import UUID

import asyncpg
import orjson

from bestfiend.contracts.mcp import ResolvedMcpServer
from bestfiend.control_plane.db import ControlPlaneDatabaseClient
from bestfiend.control_plane.mcp.errors import (
    McpConnectionConflictError,
    McpConnectionNotFoundError,
    McpStorageUnavailableError,
    McpSubscriptionConflictError,
    McpSubscriptionNotFoundError,
)
from bestfiend.control_plane.mcp.models import (
    McpConnectionRecord,
    McpServerWithSubscription,
    McpSubscriptionRecord,
)


_CONNECTION_COLUMNS = (
    "connection_id, name, url, transport, auth_type, is_public, is_system, "
    "timeout_s, supports_parallel_tool_calls, created_at, updated_at"
)
_SUBSCRIPTION_COLUMNS = (
    "user_id, connection_id, auth_token, enabled, disabled_tools, timeout_s, created_at"
)
_CONNECTION_UPDATABLE: frozenset[str] = frozenset(
    {
        "name",
        "url",
        "transport",
        "auth_type",
        "is_public",
        "timeout_s",
        "supports_parallel_tool_calls",
    }
)


class McpConnectionRepository:
    """CRUD для mcp_connections — определения MCP-серверов."""

    __slots__ = ("_db",)

    def __init__(self, db_client: ControlPlaneDatabaseClient) -> None:
        self._db = db_client

    async def create(
        self,
        *,
        name: str,
        url: str,
        transport: str = "http_stream",
        auth_type: str = "none",
        is_public: bool = False,
        is_system: bool = False,
        timeout_s: float = 30.0,
        supports_parallel_tool_calls: bool = True,
    ) -> McpConnectionRecord:
        """Создаёт подключение. UniqueViolation на name → Conflict."""
        query = (
            "INSERT INTO mcp_connections "  # nosec B608 — SQL из внутренних констант, значения через $N-параметры
            "(name, url, transport, auth_type, is_public, is_system, timeout_s, "
            "supports_parallel_tool_calls) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7, $8) "
            f"RETURNING {_CONNECTION_COLUMNS}"
        )
        try:
            row = await self._db.fetch_one(
                query,
                name,
                url,
                transport,
                auth_type,
                is_public,
                is_system,
                timeout_s,
                supports_parallel_tool_calls,
            )
        except asyncpg.UniqueViolationError as exc:
            raise McpConnectionConflictError(
                f"MCP connection name='{name}' already exists"
            ) from exc
        except asyncpg.PostgresError as exc:
            raise McpStorageUnavailableError(
                f"Failed to create mcp_connection name='{name}'"
            ) from exc
        if row is None:
            raise McpStorageUnavailableError(
                f"Create failed: no row after INSERT for name='{name}'"
            )
        return _row_to_connection(row)

    async def get_by_id(self, connection_id: UUID) -> McpConnectionRecord:
        """Возвращает подключение или бросает McpConnectionNotFoundError."""
        query = (
            f"SELECT {_CONNECTION_COLUMNS} FROM mcp_connections "  # nosec B608 — SQL из внутренних констант, значения через $N-параметры
            "WHERE connection_id = $1"
        )
        try:
            row = await self._db.fetch_one(query, connection_id)
        except asyncpg.PostgresError as exc:
            raise McpStorageUnavailableError(
                f"Failed to fetch mcp_connection id={connection_id}"
            ) from exc
        if row is None:
            raise McpConnectionNotFoundError(
                f"MCP connection id={connection_id} not found"
            )
        return _row_to_connection(row)

    async def list_all(self) -> list[McpConnectionRecord]:
        """Все подключения, отсортированы по created_at."""
        query = (
            f"SELECT {_CONNECTION_COLUMNS} FROM mcp_connections ORDER BY created_at ASC"  # nosec B608 — SQL из внутренних констант, значения через $N-параметры
        )
        try:
            rows = await self._db.fetch(query)
        except asyncpg.PostgresError as exc:
            raise McpStorageUnavailableError("Failed to list mcp_connections") from exc
        return [_row_to_connection(row) for row in rows]

    async def update(self, connection_id: UUID, **fields: Any) -> McpConnectionRecord:
        """Частичное обновление. Принимает только whitelisted поля."""
        updates: list[str] = []
        values: list[Any] = []
        for field, value in fields.items():
            if field not in _CONNECTION_UPDATABLE:
                raise ValueError(f"Unknown field '{field}' for mcp_connections.update")
            updates.append(f"{field} = ${len(values) + 2}")
            values.append(value)

        if not updates:
            return await self.get_by_id(connection_id)

        updates.append("updated_at = NOW()")
        query = (
            "UPDATE mcp_connections "  # nosec B608 — SQL из внутренних констант, значения через $N-параметры
            f"SET {', '.join(updates)} "
            "WHERE connection_id = $1 "
            f"RETURNING {_CONNECTION_COLUMNS}"
        )
        try:
            row = await self._db.fetch_one(query, connection_id, *values)
        except asyncpg.UniqueViolationError as exc:
            raise McpConnectionConflictError(
                f"MCP connection update violates unique name (id={connection_id})"
            ) from exc
        except asyncpg.PostgresError as exc:
            raise McpStorageUnavailableError(
                f"Failed to update mcp_connection id={connection_id}"
            ) from exc
        if row is None:
            raise McpConnectionNotFoundError(
                f"MCP connection id={connection_id} not found"
            )
        return _row_to_connection(row)

    async def delete(self, connection_id: UUID) -> None:
        """Удаляет подключение (CASCADE на подписки). None → NotFound.

        Guard is_system живёт уровнем выше (McpManagementService) — репозиторий
        отдаёт сырой доступ без бизнес-правил.
        """
        query = (
            "DELETE FROM mcp_connections WHERE connection_id = $1 "
            "RETURNING connection_id"
        )
        try:
            row = await self._db.fetch_one(query, connection_id)
        except asyncpg.PostgresError as exc:
            raise McpStorageUnavailableError(
                f"Failed to delete mcp_connection id={connection_id}"
            ) from exc
        if row is None:
            raise McpConnectionNotFoundError(
                f"MCP connection id={connection_id} not found"
            )


class McpSubscriptionRepository:
    """CRUD для mcp_subscriptions + резолв доступа юзера."""

    __slots__ = ("_db",)

    def __init__(self, db_client: ControlPlaneDatabaseClient) -> None:
        self._db = db_client

    async def upsert(
        self,
        user_id: UUID,
        connection_id: UUID,
        *,
        auth_token: str | None = None,
        enabled: bool = True,
        disabled_tools: list[str] | None = None,
        timeout_s: float | None = None,
    ) -> McpSubscriptionRecord:
        """Создаёт/обновляет подписку. FK violation → Conflict."""
        query = (
            "INSERT INTO mcp_subscriptions "  # nosec B608 — SQL из внутренних констант, значения через $N-параметры
            "(user_id, connection_id, auth_token, enabled, disabled_tools, timeout_s) "
            "VALUES ($1, $2, $3, $4, $5::jsonb, $6) "
            "ON CONFLICT (user_id, connection_id) DO UPDATE SET "
            "auth_token = EXCLUDED.auth_token, enabled = EXCLUDED.enabled, "
            "disabled_tools = EXCLUDED.disabled_tools, timeout_s = EXCLUDED.timeout_s "
            f"RETURNING {_SUBSCRIPTION_COLUMNS}"
        )
        tools_json = orjson.dumps(disabled_tools or []).decode("utf-8")
        try:
            row = await self._db.fetch_one(
                query,
                user_id,
                connection_id,
                auth_token,
                enabled,
                tools_json,
                timeout_s,
            )
        except asyncpg.ForeignKeyViolationError as exc:
            raise McpSubscriptionConflictError(
                f"Subscription FK violation: user_id={user_id} or "
                f"connection_id={connection_id} does not exist"
            ) from exc
        except asyncpg.PostgresError as exc:
            raise McpStorageUnavailableError(
                f"Failed to upsert mcp_subscription "
                f"user_id={user_id} connection_id={connection_id}"
            ) from exc
        if row is None:
            raise McpStorageUnavailableError("Upsert failed: no row after INSERT")
        return _row_to_subscription(row)

    async def get(
        self, user_id: UUID, connection_id: UUID
    ) -> McpSubscriptionRecord | None:
        """Возвращает подписку или None."""
        query = (
            f"SELECT {_SUBSCRIPTION_COLUMNS} FROM mcp_subscriptions "  # nosec B608 — SQL из внутренних констант, значения через $N-параметры
            "WHERE user_id = $1 AND connection_id = $2"
        )
        try:
            row = await self._db.fetch_one(query, user_id, connection_id)
        except asyncpg.PostgresError as exc:
            raise McpStorageUnavailableError(
                f"Failed to fetch mcp_subscription "
                f"user_id={user_id} connection_id={connection_id}"
            ) from exc
        return _row_to_subscription(row) if row else None

    async def delete(self, user_id: UUID, connection_id: UUID) -> None:
        """Удаляет подписку. None → NotFound."""
        query = (
            "DELETE FROM mcp_subscriptions "
            "WHERE user_id = $1 AND connection_id = $2 "
            "RETURNING connection_id"
        )
        try:
            row = await self._db.fetch_one(query, user_id, connection_id)
        except asyncpg.PostgresError as exc:
            raise McpStorageUnavailableError(
                f"Failed to delete mcp_subscription "
                f"user_id={user_id} connection_id={connection_id}"
            ) from exc
        if row is None:
            raise McpSubscriptionNotFoundError(
                f"Subscription user_id={user_id} connection_id={connection_id} not found"
            )

    async def list_for_user(self, user_id: UUID) -> list[ResolvedMcpServer]:
        """Резолв доступа: public ∪ private-с-подпиской, минус выключенные подпиской.

        Denylist `disabled_tools` отдаётся полем — вычитание тулзов из каталога
        происходит на сборке инструментов (L3).
        """
        query = (
            "SELECT c.connection_id, c.name, c.url, c.transport, c.auth_type, "
            "COALESCE(s.timeout_s, c.timeout_s) AS timeout_s, c.is_public, s.auth_token, "
            "COALESCE(s.disabled_tools, '[]'::jsonb) AS disabled_tools, "
            "c.supports_parallel_tool_calls "
            "FROM mcp_connections c "
            "LEFT JOIN mcp_subscriptions s "
            "ON s.connection_id = c.connection_id AND s.user_id = $1 "
            "WHERE (c.is_public = true OR s.user_id IS NOT NULL) "
            "AND COALESCE(s.enabled, true) = true "
            "ORDER BY c.created_at ASC"
        )
        try:
            rows = await self._db.fetch(query, user_id)
        except asyncpg.PostgresError as exc:
            raise McpStorageUnavailableError(
                f"Failed to resolve mcp servers for user_id={user_id}"
            ) from exc
        return [_row_to_resolved(row) for row in rows]

    async def list_visible_for_user(
        self, user_id: UUID
    ) -> list[McpServerWithSubscription]:
        """Видимые серверы для UI: public ∪ subscribed, дефолты и оверрайды РАЗДЕЛЬНО.

        В отличие от list_for_user: НЕ COALESCE-ит timeout/denylist (UI показывает
        дефолт сервера и персональный оверрайд порознь) и НЕ фильтрует по enabled
        (выключенные надо видеть, чтобы включить). `has_subscription` — по наличию
        строки подписки (s.user_id), не по auth_token.
        """
        query = (
            "SELECT c.connection_id, c.name, c.url, c.transport, c.auth_type, "
            "c.is_public, c.is_system, c.timeout_s, "
            "(s.user_id IS NOT NULL) AS has_subscription, "
            "s.enabled AS sub_enabled, s.auth_token AS sub_auth_token, "
            "s.disabled_tools AS sub_disabled_tools, "
            "s.timeout_s AS sub_timeout_s, s.created_at AS sub_created_at "
            "FROM mcp_connections c "
            "LEFT JOIN mcp_subscriptions s "
            "ON s.connection_id = c.connection_id AND s.user_id = $1 "
            "WHERE (c.is_public = true OR s.user_id IS NOT NULL) "
            "ORDER BY c.created_at ASC"
        )
        try:
            rows = await self._db.fetch(query, user_id)
        except asyncpg.PostgresError as exc:
            raise McpStorageUnavailableError(
                f"Failed to list visible mcp servers for user_id={user_id}"
            ) from exc
        return [_row_to_visible(row) for row in rows]


def _row_to_connection(row: Any) -> McpConnectionRecord:
    return McpConnectionRecord(
        connection_id=row["connection_id"],
        name=row["name"],
        url=row["url"],
        transport=row["transport"],
        auth_type=row["auth_type"],
        is_public=row["is_public"],
        is_system=row["is_system"],
        timeout_s=row["timeout_s"],
        supports_parallel_tool_calls=row["supports_parallel_tool_calls"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_subscription(row: Any) -> McpSubscriptionRecord:
    return McpSubscriptionRecord(
        user_id=row["user_id"],
        connection_id=row["connection_id"],
        auth_token=row["auth_token"],
        enabled=row["enabled"],
        disabled_tools=_parse_tools(row["disabled_tools"]),
        timeout_s=row["timeout_s"],
        created_at=row["created_at"],
    )


def _row_to_resolved(row: Any) -> ResolvedMcpServer:
    return ResolvedMcpServer(
        connection_id=row["connection_id"],
        name=row["name"],
        url=row["url"],
        transport=row["transport"],
        auth_type=row["auth_type"],
        timeout_s=row["timeout_s"],
        is_public=row["is_public"],
        # none ⇒ токен не применяется: легаси-остаток auth_token в подписке
        # (после смены auth_type подключения) не должен уезжать в заголовок.
        auth_token=None if row["auth_type"] == "none" else row["auth_token"],
        disabled_tools=_parse_tools(row["disabled_tools"]),
        supports_parallel_tool_calls=row["supports_parallel_tool_calls"],
    )


def _row_to_visible(row: Any) -> McpServerWithSubscription:
    # sub_* читаем только при наличии подписки: none-auth подписка несёт token=NULL,
    # но строка есть — маркер has_subscription, не auth_token.
    has_sub = row["has_subscription"]
    return McpServerWithSubscription(
        connection_id=row["connection_id"],
        name=row["name"],
        url=row["url"],
        transport=row["transport"],
        auth_type=row["auth_type"],
        is_public=row["is_public"],
        is_system=row["is_system"],
        timeout_s=row["timeout_s"],
        has_subscription=has_sub,
        sub_enabled=row["sub_enabled"] if has_sub else None,
        sub_auth_token=row["sub_auth_token"] if has_sub else None,
        sub_disabled_tools=_parse_tools(row["sub_disabled_tools"]) if has_sub else None,
        sub_timeout_s=row["sub_timeout_s"] if has_sub else None,
        sub_created_at=row["sub_created_at"] if has_sub else None,
    )


def _parse_tools(raw: Any) -> list[str]:
    """JSONB disabled_tools → list[str] (asyncpg отдаёт str или уже распарсенный)."""
    if isinstance(raw, str):
        return list(orjson.loads(raw))
    return list(raw) if raw else []
