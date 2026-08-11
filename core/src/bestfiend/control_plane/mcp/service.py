"""Сервисный слой MCP-management: connections (admin), subscriptions (user), preview.

`McpManagementService` держит бизнес-инварианты поверх L1-репозиториев: public⇒
auth_type in (none, oauth), запрет удаления is_system, видимость серверов для preview.
OAuth-клиенты и статусы добираются через `McpOAuthService`. Preview переиспользует
L2 `discover_servers` (graceful, фейл в `.failure`).
"""

from typing import Any
from uuid import UUID

from loguru import logger

from bestfiend.contracts.mcp import McpAuthType, McpTransport, ResolvedMcpServer
from bestfiend.control_plane.mcp.errors import (
    McpConnectionNotFoundError,
    McpSystemConnectionError,
    McpValidationError,
)
from bestfiend.control_plane.mcp.models import (
    McpConnectionWithOAuthClient,
    McpServerWithSubscription,
    McpSubscriptionRecord,
)
from bestfiend.control_plane.mcp.oauth.models import McpOAuthClientRecord
from bestfiend.control_plane.mcp.oauth.service import McpOAuthService
from bestfiend.control_plane.mcp.repository import (
    McpConnectionRepository,
    McpSubscriptionRepository,
)
from bestfiend.mcp.contracts import ServerDiscovery
from bestfiend.mcp.discovery import discover_servers
from bestfiend.mcp.settings import McpDiscoverySettings


# Фиктивный id ad-hoc preview-сервера: наружу не уходит (router отдаёт None для ad-hoc).
_PREVIEW_CONNECTION_ID = UUID(int=0)
# Дефолт preview: discovery применяет ГЛОБАЛЬНЫЙ таймаут, не это поле; ResolvedMcpServer
# требует timeout_s — кладём дефолт сервера.
_PREVIEW_TIMEOUT_S = 30.0


class McpManagementService:
    """Бизнес-логика управления MCP-подключениями и подписками."""

    __slots__ = ("_connections", "_subscriptions", "_oauth", "_discovery_settings")

    def __init__(
        self,
        *,
        connection_repository: McpConnectionRepository,
        subscription_repository: McpSubscriptionRepository,
        oauth_service: McpOAuthService,
        discovery_settings: McpDiscoverySettings,
    ) -> None:
        self._connections = connection_repository
        self._subscriptions = subscription_repository
        self._oauth = oauth_service
        self._discovery_settings = discovery_settings

    # ----- connections (admin) -----

    async def list_connections(self) -> list[McpConnectionWithOAuthClient]:
        """Все подключения (admin-обзор) с состоянием OAuth-клиента для oauth-серверов."""
        records = await self._connections.list_all()
        oauth_ids = [r.connection_id for r in records if r.auth_type == "oauth"]
        clients = await self._oauth.get_clients(oauth_ids)
        return [
            McpConnectionWithOAuthClient(
                connection=record, oauth_client=clients.get(record.connection_id)
            )
            for record in records
        ]

    async def create_connection(
        self,
        *,
        name: str,
        url: str,
        transport: McpTransport,
        auth_type: McpAuthType,
        is_public: bool,
        timeout_s: float,
        supports_parallel_tool_calls: bool,
        oauth_client_id: str | None = None,
        oauth_client_secret: str | None = None,
    ) -> McpConnectionWithOAuthClient:
        """Создаёт подключение. Инвариант: public ⇒ auth_type in (none, oauth). is_system всегда false."""
        self._enforce_public_auth(is_public=is_public, auth_type=auth_type)
        oauth_client_id, oauth_client_secret = self._normalize_oauth_credentials(
            auth_type=auth_type,
            client_id=oauth_client_id,
            client_secret=oauth_client_secret,
        )
        record = await self._connections.create(
            name=name,
            url=url,
            transport=transport,
            auth_type=auth_type,
            is_public=is_public,
            timeout_s=timeout_s,
            supports_parallel_tool_calls=supports_parallel_tool_calls,
        )
        oauth_client = await self._resolve_oauth_client(
            record.connection_id,
            auth_type=record.auth_type,
            client_id=oauth_client_id,
            client_secret=oauth_client_secret,
        )
        logger.info(
            "McpManagementService: created connection id={}", record.connection_id
        )
        return McpConnectionWithOAuthClient(
            connection=record, oauth_client=oauth_client
        )

    async def update_connection(
        self,
        connection_id: UUID,
        fields: dict[str, Any],
        *,
        oauth_client_id: str | None = None,
        oauth_client_secret: str | None = None,
    ) -> McpConnectionWithOAuthClient:
        """Частичное обновление. Инвариант проверяется на ЭФФЕКТИВНЫХ полях (текущие + патч)."""
        current = await self._connections.get_by_id(connection_id)
        eff_public = bool(fields.get("is_public", current.is_public))
        eff_auth = str(fields.get("auth_type", current.auth_type))
        self._enforce_public_auth(is_public=eff_public, auth_type=eff_auth)
        oauth_client_id, oauth_client_secret = self._normalize_oauth_credentials(
            auth_type=eff_auth,
            client_id=oauth_client_id,
            client_secret=oauth_client_secret,
        )
        record = current if not fields else await self._connections.update(
            connection_id, **fields
        )
        # Смена auth_type с oauth клиента и связку не трогает (мусор безвреден, CASCADE
        # снесёт при delete); для не-oauth в выдачу клиент не попадает.
        oauth_client = await self._resolve_oauth_client(
            connection_id,
            auth_type=eff_auth,
            client_id=oauth_client_id,
            client_secret=oauth_client_secret,
        )
        return McpConnectionWithOAuthClient(
            connection=record, oauth_client=oauth_client
        )

    async def delete_connection(self, connection_id: UUID) -> None:
        """Удаляет подключение. is_system защищено (юзер не сносит системный сервер)."""
        record = await self._connections.get_by_id(connection_id)
        if record.is_system:
            raise McpSystemConnectionError(
                f"connection id={connection_id} is system-protected; delete forbidden"
            )
        await self._connections.delete(connection_id)
        logger.info("McpManagementService: deleted connection id={}", connection_id)

    @staticmethod
    def _enforce_public_auth(*, is_public: bool, auth_type: str) -> None:
        """public-сервер виден всем; допустимы none (без токена) и oauth (токен per-юзер)."""
        if is_public and auth_type not in ("none", "oauth"):
            raise McpValidationError(
                f"public connection must use auth_type in (none, oauth) (got '{auth_type}')"
            )

    @staticmethod
    def _normalize_oauth_credentials(
        *, auth_type: str, client_id: str | None, client_secret: str | None
    ) -> tuple[str | None, str | None]:
        """Нормализует OAuth-креды (пустые строки → None) и проверяет применимость.

        Секрет без client_id отклоняется: ротация секрета — всегда пара id+secret,
        молча терять секрет нельзя. Креды вне auth_type='oauth' — ошибка валидации.
        """
        client_id = (client_id or "").strip() or None
        client_secret = (client_secret or "").strip() or None
        if auth_type != "oauth" and (
            client_id is not None or client_secret is not None
        ):
            raise McpValidationError(
                f"oauth credentials require auth_type='oauth' (got '{auth_type}')"
            )
        if client_secret is not None and client_id is None:
            raise McpValidationError(
                "oauth_client_secret требует oauth_client_id: секрет без клиента "
                "сохранить нельзя"
            )
        return client_id, client_secret

    async def _resolve_oauth_client(
        self,
        connection_id: UUID,
        *,
        auth_type: str,
        client_id: str | None,
        client_secret: str | None,
    ) -> McpOAuthClientRecord | None:
        """Состояние OAuth-клиента для admin-выдачи: upsert кред либо текущая запись."""
        if auth_type != "oauth":
            return None
        if client_id is not None:
            return await self._oauth.upsert_preregistered_client(
                connection_id, client_id=client_id, client_secret=client_secret
            )
        return await self._oauth.get_client(connection_id)

    # ----- subscriptions (user) -----

    async def list_my_servers(self, user_id: UUID) -> list[McpServerWithSubscription]:
        """Видимые юзеру серверы (public ∪ подписки) + оверрайды и oauth-статус."""
        servers = await self._subscriptions.list_visible_for_user(user_id)
        oauth_ids = [s.connection_id for s in servers if s.auth_type == "oauth"]
        if not oauth_ids:
            return servers
        statuses = await self._oauth.status_for(user_id, oauth_ids)
        return [
            server.model_copy(
                update={"oauth_status": statuses.get(server.connection_id)}
            )
            if server.auth_type == "oauth"
            else server
            for server in servers
        ]

    async def upsert_subscription(
        self,
        user_id: UUID,
        connection_id: UUID,
        *,
        enabled: bool,
        auth_token: str | None,
        disabled_tools: list[str],
        timeout_s: float | None,
    ) -> McpSubscriptionRecord:
        """Создаёт/заменяет подписку юзера. Несуществующий connection → FK Conflict (репо)."""
        record = await self._subscriptions.upsert(
            user_id,
            connection_id,
            auth_token=auth_token,
            enabled=enabled,
            disabled_tools=disabled_tools,
            timeout_s=timeout_s,
        )
        logger.info(
            "McpManagementService: upserted subscription user={} connection={}",
            user_id,
            connection_id,
        )
        return record

    async def delete_subscription(self, user_id: UUID, connection_id: UUID) -> None:
        """Удаляет подписку. Доступ к public не теряется (остаётся виден через is_public)."""
        await self._subscriptions.delete(user_id, connection_id)
        logger.info(
            "McpManagementService: deleted subscription user={} connection={}",
            user_id,
            connection_id,
        )

    # ----- discover-preview -----

    async def discover_preview(
        self,
        *,
        is_admin: bool,
        user_id: UUID,
        connection_id: UUID | None,
        url: str | None,
        auth_type: McpAuthType | None,
        auth_token: str | None,
    ) -> ServerDiscovery:
        """Опрашивает сервер на лету (test-connection). Фейл — в .failure, не исключение."""
        server = await self._build_preview_server(
            is_admin=is_admin,
            user_id=user_id,
            connection_id=connection_id,
            url=url,
            auth_type=auth_type,
            auth_token=auth_token,
        )
        results = await discover_servers([server], self._discovery_settings)
        return results[0]

    async def _build_preview_server(
        self,
        *,
        is_admin: bool,
        user_id: UUID,
        connection_id: UUID | None,
        url: str | None,
        auth_type: McpAuthType | None,
        auth_token: str | None,
    ) -> ResolvedMcpServer:
        """Собирает ResolvedMcpServer для preview: by-id (доверенный url) или ad-hoc (admin)."""
        if connection_id is not None:
            conn = await self._connections.get_by_id(connection_id)
            sub = await self._subscriptions.get(user_id, connection_id)
            # Видимость: public, своя подписка, либо admin (видит всё). Иначе — не светим private.
            if not conn.is_public and sub is None and not is_admin:
                raise McpConnectionNotFoundError(
                    f"MCP connection id={connection_id} not visible to user"
                )
            if conn.auth_type == "oauth":
                # Живой access юзера; нет токена → discovery упадёт в failure "auth" штатно.
                token = await self._oauth.fresh_access_token(user_id, connection_id)
            elif conn.auth_type == "bearer":
                token = sub.auth_token if sub is not None else None
            else:
                # none ⇒ без auth: легаси-токен подписки игнорируется (паритет _row_to_resolved).
                token = None
            return ResolvedMcpServer(
                connection_id=conn.connection_id,
                name=conn.name,
                url=conn.url,
                transport=conn.transport,
                auth_type=conn.auth_type,
                timeout_s=conn.timeout_s,
                is_public=conn.is_public,
                auth_token=token,
                disabled_tools=[],
            )
        # Ad-hoc url — только admin (SSRF-барьер; defense-in-depth поверх router-guard).
        if not is_admin:
            raise McpValidationError("ad-hoc url preview is admin-only")
        if url is None:
            raise McpValidationError("ad-hoc preview requires url")
        effective_auth_type = auth_type or "none"
        return ResolvedMcpServer(
            connection_id=_PREVIEW_CONNECTION_ID,
            name="preview",
            url=url,
            transport="http_stream",
            auth_type=effective_auth_type,
            timeout_s=_PREVIEW_TIMEOUT_S,
            is_public=False,
            # none ⇒ без auth даже если токен прислали (паритет _row_to_resolved).
            auth_token=None if effective_auth_type == "none" else auth_token,
            disabled_tools=[],
        )
