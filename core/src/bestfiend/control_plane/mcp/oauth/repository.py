"""PostgreSQL repositories OAuth-тракта MCP: clients + flows + tokens.

McpOAuthClientRepository — CRUD OAuth-клиента per connection.
McpOAuthFlowRepository — незавершённые авторизации; `take` — одноразовое чтение
через DELETE ... RETURNING; `purge_expired` чистит протухшие.
McpOAuthTokenRepository — токены per (user, connection) + CAS-запись против гонки
ротации refresh (update_tokens_if_refresh_matches / mark_refresh_failed).

asyncpg.PostgresError транслируется в McpStorageUnavailableError — общий
storage-контракт модуля mcp (переиспользуется, не дублируется).
"""

from datetime import datetime
from typing import Any
from uuid import UUID

import asyncpg

from bestfiend.control_plane.db import ControlPlaneDatabaseClient
from bestfiend.control_plane.mcp.errors import McpStorageUnavailableError
from bestfiend.control_plane.mcp.oauth.models import (
    McpOAuthClientRecord,
    McpOAuthFlowRecord,
    McpOAuthTokenRecord,
)


_CLIENT_COLUMNS = (
    "connection_id, client_id, client_secret, token_endpoint_auth_method, "
    "source, client_secret_expires_at, created_at, updated_at"
)
_FLOW_COLUMNS = (
    "state, user_id, connection_id, code_verifier, redirect_uri, token_endpoint, "
    "issuer, resource, scope, expires_at, created_at"
)
_TOKEN_COLUMNS = (
    "user_id, connection_id, access_token, refresh_token, expires_at, scope, "  # nosec B105 — список SQL-колонок, не секрет
    "token_endpoint, refresh_failed_at, created_at, updated_at"
)


class McpOAuthClientRepository:
    """CRUD для mcp_oauth_clients — OAuth-клиент per connection."""

    __slots__ = ("_db",)

    def __init__(self, db_client: ControlPlaneDatabaseClient) -> None:
        self._db = db_client

    async def get(self, connection_id: UUID) -> McpOAuthClientRecord | None:
        """Возвращает OAuth-клиента connection или None."""
        query = (
            f"SELECT {_CLIENT_COLUMNS} FROM mcp_oauth_clients "  # nosec B608 — SQL из внутренних констант, значения через $N-параметры
            "WHERE connection_id = $1"
        )
        try:
            row = await self._db.fetch_one(query, connection_id)
        except asyncpg.PostgresError as exc:
            raise McpStorageUnavailableError(
                f"Failed to fetch mcp_oauth_client connection_id={connection_id}"
            ) from exc
        return _row_to_client(row) if row else None

    async def upsert(
        self,
        connection_id: UUID,
        *,
        client_id: str,
        client_secret: str | None,
        token_endpoint_auth_method: str,
        source: str,
        client_secret_expires_at: datetime | None = None,
    ) -> McpOAuthClientRecord:
        """Создаёт/обновляет OAuth-клиента connection."""
        query = (
            "INSERT INTO mcp_oauth_clients "  # nosec B608 — SQL из внутренних констант, значения через $N-параметры
            "(connection_id, client_id, client_secret, token_endpoint_auth_method, "
            "source, client_secret_expires_at) "
            "VALUES ($1, $2, $3, $4, $5, $6) "
            "ON CONFLICT (connection_id) DO UPDATE SET "
            "client_id = EXCLUDED.client_id, client_secret = EXCLUDED.client_secret, "
            "token_endpoint_auth_method = EXCLUDED.token_endpoint_auth_method, "
            "source = EXCLUDED.source, "
            "client_secret_expires_at = EXCLUDED.client_secret_expires_at, "
            "updated_at = now() "
            f"RETURNING {_CLIENT_COLUMNS}"
        )
        try:
            row = await self._db.fetch_one(
                query,
                connection_id,
                client_id,
                client_secret,
                token_endpoint_auth_method,
                source,
                client_secret_expires_at,
            )
        except asyncpg.PostgresError as exc:
            raise McpStorageUnavailableError(
                f"Failed to upsert mcp_oauth_client connection_id={connection_id}"
            ) from exc
        if row is None:
            raise McpStorageUnavailableError(
                f"Upsert failed: no row after INSERT for connection_id={connection_id}"
            )
        return _row_to_client(row)

    async def delete(self, connection_id: UUID) -> None:
        """Удаляет OAuth-клиента connection. Идемпотентно (нет строки — no-op)."""
        query = "DELETE FROM mcp_oauth_clients WHERE connection_id = $1"
        try:
            await self._db.execute(query, connection_id)
        except asyncpg.PostgresError as exc:
            raise McpStorageUnavailableError(
                f"Failed to delete mcp_oauth_client connection_id={connection_id}"
            ) from exc


class McpOAuthFlowRepository:
    """CRUD для mcp_oauth_flows — незавершённые авторизации, одноразовые."""

    __slots__ = ("_db",)

    def __init__(self, db_client: ControlPlaneDatabaseClient) -> None:
        self._db = db_client

    async def create(
        self,
        *,
        state: str,
        user_id: UUID,
        connection_id: UUID,
        code_verifier: str,
        redirect_uri: str,
        token_endpoint: str,
        issuer: str,
        resource: str,
        scope: str | None,
        expires_at: datetime,
    ) -> McpOAuthFlowRecord:
        """Создаёт flow-запись авторизации (одноразовую, с TTL)."""
        query = (
            "INSERT INTO mcp_oauth_flows "  # nosec B608 — SQL из внутренних констант, значения через $N-параметры
            "(state, user_id, connection_id, code_verifier, redirect_uri, "
            "token_endpoint, issuer, resource, scope, expires_at) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10) "
            f"RETURNING {_FLOW_COLUMNS}"
        )
        try:
            row = await self._db.fetch_one(
                query,
                state,
                user_id,
                connection_id,
                code_verifier,
                redirect_uri,
                token_endpoint,
                issuer,
                resource,
                scope,
                expires_at,
            )
        except asyncpg.PostgresError as exc:
            raise McpStorageUnavailableError(
                f"Failed to create mcp_oauth_flow connection_id={connection_id}"
            ) from exc
        if row is None:
            raise McpStorageUnavailableError(
                "Create failed: no row after INSERT for mcp_oauth_flow"
            )
        return _row_to_flow(row)

    async def take(self, state: str) -> McpOAuthFlowRecord | None:
        """Читает и удаляет flow одним DELETE ... RETURNING (атомарная одноразовость).

        Повторный вызов с тем же state вернёт None — запись уже погашена.
        """
        query = (
            "DELETE FROM mcp_oauth_flows WHERE state = $1 "  # nosec B608 — SQL из внутренних констант, значения через $N-параметры
            f"RETURNING {_FLOW_COLUMNS}"
        )
        try:
            row = await self._db.fetch_one(query, state)
        except asyncpg.PostgresError as exc:
            raise McpStorageUnavailableError(
                "Failed to take mcp_oauth_flow"
            ) from exc
        return _row_to_flow(row) if row else None

    async def purge_expired(self) -> int:
        """Удаляет протухшие flow-записи, возвращает число удалённых строк."""
        query = "DELETE FROM mcp_oauth_flows WHERE expires_at < now()"
        try:
            status = await self._db.execute(query)
        except asyncpg.PostgresError as exc:
            raise McpStorageUnavailableError(
                "Failed to purge expired mcp_oauth_flows"
            ) from exc
        return _command_rowcount(status)


class McpOAuthTokenRepository:
    """CRUD для mcp_oauth_tokens + CAS-запись против гонки ротации refresh."""

    __slots__ = ("_db",)

    def __init__(self, db_client: ControlPlaneDatabaseClient) -> None:
        self._db = db_client

    async def get(
        self, user_id: UUID, connection_id: UUID
    ) -> McpOAuthTokenRecord | None:
        """Возвращает токены (user, connection) или None."""
        query = (
            f"SELECT {_TOKEN_COLUMNS} FROM mcp_oauth_tokens "  # nosec B608 — SQL из внутренних констант, значения через $N-параметры
            "WHERE user_id = $1 AND connection_id = $2"
        )
        try:
            row = await self._db.fetch_one(query, user_id, connection_id)
        except asyncpg.PostgresError as exc:
            raise McpStorageUnavailableError(
                f"Failed to fetch mcp_oauth_token "
                f"user_id={user_id} connection_id={connection_id}"
            ) from exc
        return _row_to_token(row) if row else None

    async def list_for_user(self, user_id: UUID) -> list[McpOAuthTokenRecord]:
        """Все токен-записи юзера (для батч-статуса на /mcp)."""
        query = (
            f"SELECT {_TOKEN_COLUMNS} FROM mcp_oauth_tokens "  # nosec B608 — SQL из внутренних констант, значения через $N-параметры
            "WHERE user_id = $1"
        )
        try:
            rows = await self._db.fetch(query, user_id)
        except asyncpg.PostgresError as exc:
            raise McpStorageUnavailableError(
                f"Failed to list mcp_oauth_tokens for user_id={user_id}"
            ) from exc
        return [_row_to_token(row) for row in rows]

    async def upsert(
        self,
        user_id: UUID,
        connection_id: UUID,
        *,
        access_token: str,
        refresh_token: str | None,
        expires_at: datetime | None,
        scope: str | None,
        token_endpoint: str,
    ) -> McpOAuthTokenRecord:
        """Создаёт/обновляет токены. Успешная запись сбрасывает refresh_failed_at."""
        query = (
            "INSERT INTO mcp_oauth_tokens "  # nosec B608 — SQL из внутренних констант, значения через $N-параметры
            "(user_id, connection_id, access_token, refresh_token, expires_at, "
            "scope, token_endpoint) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7) "
            "ON CONFLICT (user_id, connection_id) DO UPDATE SET "
            "access_token = EXCLUDED.access_token, "
            "refresh_token = EXCLUDED.refresh_token, "
            "expires_at = EXCLUDED.expires_at, scope = EXCLUDED.scope, "
            "token_endpoint = EXCLUDED.token_endpoint, "
            "refresh_failed_at = NULL, updated_at = now() "
            f"RETURNING {_TOKEN_COLUMNS}"
        )
        try:
            row = await self._db.fetch_one(
                query,
                user_id,
                connection_id,
                access_token,
                refresh_token,
                expires_at,
                scope,
                token_endpoint,
            )
        except asyncpg.PostgresError as exc:
            raise McpStorageUnavailableError(
                f"Failed to upsert mcp_oauth_token "
                f"user_id={user_id} connection_id={connection_id}"
            ) from exc
        if row is None:
            raise McpStorageUnavailableError(
                "Upsert failed: no row after INSERT for mcp_oauth_token"
            )
        return _row_to_token(row)

    async def delete(self, user_id: UUID, connection_id: UUID) -> None:
        """Удаляет токены (user, connection). Идемпотентно (нет строки — no-op)."""
        query = (
            "DELETE FROM mcp_oauth_tokens WHERE user_id = $1 AND connection_id = $2"
        )
        try:
            await self._db.execute(query, user_id, connection_id)
        except asyncpg.PostgresError as exc:
            raise McpStorageUnavailableError(
                f"Failed to delete mcp_oauth_token "
                f"user_id={user_id} connection_id={connection_id}"
            ) from exc

    async def update_tokens_if_refresh_matches(
        self,
        user_id: UUID,
        connection_id: UUID,
        *,
        access_token: str,
        refresh_token: str | None,
        expires_at: datetime | None,
        scope: str | None,
        expected_refresh_token: str,
    ) -> bool:
        """CAS-запись обновлённых токенов: пишет только если refresh не сменился.

        WHERE refresh_token = expected. 0 строк (False) = параллельный refresh
        уже обновил запись — вызывающий перечитывает. Успех сбрасывает
        refresh_failed_at.
        """
        query = (
            "UPDATE mcp_oauth_tokens SET "  # nosec B608 — SQL из внутренних констант, значения через $N-параметры
            "access_token = $3, refresh_token = $4, expires_at = $5, scope = $6, "
            "refresh_failed_at = NULL, updated_at = now() "
            "WHERE user_id = $1 AND connection_id = $2 AND refresh_token = $7"
        )
        try:
            status = await self._db.execute(
                query,
                user_id,
                connection_id,
                access_token,
                refresh_token,
                expires_at,
                scope,
                expected_refresh_token,
            )
        except asyncpg.PostgresError as exc:
            raise McpStorageUnavailableError(
                f"Failed to CAS-update mcp_oauth_token "
                f"user_id={user_id} connection_id={connection_id}"
            ) from exc
        return _command_rowcount(status) > 0

    async def mark_refresh_failed(
        self,
        user_id: UUID,
        connection_id: UUID,
        *,
        expected_refresh_token: str,
    ) -> bool:
        """CAS-клеймо отказа refresh: помечает только если refresh не сменился.

        WHERE refresh_token = expected. 0 строк (False) = запись уже обновлена
        соседом — свежий токен клеймить устаревшим invalid_grant нельзя.
        """
        query = (
            "UPDATE mcp_oauth_tokens SET refresh_failed_at = now(), updated_at = now() "  # nosec B608 — SQL из внутренних констант, значения через $N-параметры
            "WHERE user_id = $1 AND connection_id = $2 AND refresh_token = $3"
        )
        try:
            status = await self._db.execute(
                query, user_id, connection_id, expected_refresh_token
            )
        except asyncpg.PostgresError as exc:
            raise McpStorageUnavailableError(
                f"Failed to mark refresh failed for mcp_oauth_token "
                f"user_id={user_id} connection_id={connection_id}"
            ) from exc
        return _command_rowcount(status) > 0


def _row_to_client(row: Any) -> McpOAuthClientRecord:
    return McpOAuthClientRecord(
        connection_id=row["connection_id"],
        client_id=row["client_id"],
        client_secret=row["client_secret"],
        token_endpoint_auth_method=row["token_endpoint_auth_method"],
        source=row["source"],
        client_secret_expires_at=row["client_secret_expires_at"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_flow(row: Any) -> McpOAuthFlowRecord:
    return McpOAuthFlowRecord(
        state=row["state"],
        user_id=row["user_id"],
        connection_id=row["connection_id"],
        code_verifier=row["code_verifier"],
        redirect_uri=row["redirect_uri"],
        token_endpoint=row["token_endpoint"],
        issuer=row["issuer"],
        resource=row["resource"],
        scope=row["scope"],
        expires_at=row["expires_at"],
        created_at=row["created_at"],
    )


def _row_to_token(row: Any) -> McpOAuthTokenRecord:
    return McpOAuthTokenRecord(
        user_id=row["user_id"],
        connection_id=row["connection_id"],
        access_token=row["access_token"],
        refresh_token=row["refresh_token"],
        expires_at=row["expires_at"],
        scope=row["scope"],
        token_endpoint=row["token_endpoint"],
        refresh_failed_at=row["refresh_failed_at"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _command_rowcount(status: str) -> int:
    """Число затронутых строк из command tag asyncpg ('UPDATE 1', 'DELETE 3')."""
    parts = status.split()
    if not parts:
        return 0
    try:
        return int(parts[-1])
    except ValueError:
        return 0
