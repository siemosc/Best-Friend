"""Оркестратор split-flow OAuth-авторизации в MCP-серверах.

Состояние между «выдать authorization URL» (start_flow) и «обменять code»
(complete_flow) живёт в Postgres — поток разорван на два HTTP-запроса. Refresh
токенов при резолве (fresh_access_token) идёт под per-(user, connection) блокировкой
и CAS-записью против гонки ротации refresh_token.
"""

import asyncio
from datetime import UTC, datetime, timedelta
import secrets
from urllib.parse import urlencode
from uuid import UUID

from mcp.client.auth.oauth2 import PKCEParameters
from mcp.shared.auth_utils import resource_url_from_server_url

from bestfiend.control_plane.mcp.errors import (
    McpConnectionNotFoundError,
    McpValidationError,
)
from bestfiend.control_plane.mcp.models import McpConnectionRecord
from bestfiend.control_plane.mcp.oauth.discovery import (
    AuthorizationServerInfo,
    discover_authorization_server,
)
from bestfiend.control_plane.mcp.oauth.errors import (
    McpOAuthClientMissingError,
    McpOAuthDiscoveryError,
    McpOAuthExchangeError,
    McpOAuthFlowNotFoundError,
    McpOAuthRefreshRejectedError,
    McpOAuthRegistrationError,
)
from bestfiend.control_plane.mcp.oauth.models import (
    McpOAuthClientRecord,
    McpOAuthStatus,
    McpOAuthTokenRecord,
)
from bestfiend.control_plane.mcp.oauth.repository import (
    McpOAuthClientRepository,
    McpOAuthFlowRepository,
    McpOAuthTokenRepository,
)
from bestfiend.control_plane.mcp.oauth.token_client import OAuthTokenClient
from bestfiend.control_plane.mcp.repository import (
    McpConnectionRepository,
    McpSubscriptionRepository,
)


_ACCESS_SKEW_S = 60  # access считается протухшим за 60 с до формального срока
_VALID_AUTH_METHODS = frozenset(
    {"client_secret_basic", "client_secret_post", "none"}
)
_PKCE_METHOD = "S256"


class McpOAuthService:
    """Владелец split-flow: старт авторизации, обмен code, refresh, статусы."""

    __slots__ = (
        "_clients",
        "_flows",
        "_tokens",
        "_connections",
        "_subscriptions",
        "_token_client",
        "_redirect_uri",
        "_flow_ttl_s",
        "_refresh_locks",
    )

    def __init__(
        self,
        *,
        client_repository: McpOAuthClientRepository,
        flow_repository: McpOAuthFlowRepository,
        token_repository: McpOAuthTokenRepository,
        connection_repository: McpConnectionRepository,
        subscription_repository: McpSubscriptionRepository,
        token_client: OAuthTokenClient,
        redirect_uri: str,
        flow_ttl_s: float = 600,
    ) -> None:
        self._clients = client_repository
        self._flows = flow_repository
        self._tokens = token_repository
        self._connections = connection_repository
        self._subscriptions = subscription_repository
        self._token_client = token_client
        self._redirect_uri = redirect_uri
        self._flow_ttl_s = flow_ttl_s
        self._refresh_locks: dict[tuple[UUID, UUID], asyncio.Lock] = {}

    async def start_flow(self, user_id: UUID, connection_id: UUID) -> str:
        """Готовит авторизацию: discovery, клиент, PKCE, flow-запись, authorization URL."""
        connection = await self._connections.get_by_id(connection_id)
        # Невидимое подключение = «не существует» для юзера (как в management-сервисе),
        # неверный auth_type — ошибка валидации запроса, не OAuth-тракта.
        if not await self._is_visible(user_id, connection):
            raise McpConnectionNotFoundError(f"id={connection_id} not found")
        if connection.auth_type != "oauth":
            raise McpValidationError(
                f"connection {connection_id}: auth_type "
                f"'{connection.auth_type}', OAuth-подключение требует 'oauth'"
            )

        info = await discover_authorization_server(
            connection.url, timeout_s=connection.timeout_s
        )
        _require_pkce_s256(info)

        client = await self._resolve_client(connection_id, info)
        scope = _select_scope(info)
        resource = resource_url_from_server_url(connection.url)
        pkce = PKCEParameters.generate()
        state = secrets.token_urlsafe(32)

        authorization_url = _build_authorization_url(
            authorization_endpoint=info.authorization_endpoint,
            client_id=client.client_id,
            redirect_uri=self._redirect_uri,
            state=state,
            code_challenge=pkce.code_challenge,
            resource=resource,
            scope=scope,
        )

        await self._flows.create(
            state=state,
            user_id=user_id,
            connection_id=connection_id,
            code_verifier=pkce.code_verifier,
            redirect_uri=self._redirect_uri,
            token_endpoint=info.token_endpoint,
            issuer=info.issuer,
            resource=resource,
            scope=scope,
            expires_at=datetime.now(UTC) + timedelta(seconds=self._flow_ttl_s),
        )
        await self._flows.purge_expired()
        return authorization_url

    async def complete_flow(
        self, user_id: UUID, state: str, code: str, issuer: str | None
    ) -> McpConnectionRecord:
        """Гасит flow по state, обменивает code на токены, возвращает connection."""
        flow = await self._flows.take(state)
        if flow is None:
            raise McpOAuthFlowNotFoundError(f"OAuth flow state={state} not found")
        if flow.expires_at < datetime.now(UTC):
            raise McpOAuthFlowNotFoundError(f"OAuth flow state={state} expired")
        if flow.user_id != user_id:
            raise McpOAuthFlowNotFoundError(
                f"OAuth flow state={state} belongs to another user"
            )
        # issuer из метадаты AS нормализован pydantic (trailing slash), `iss` из
        # callback приходит сырым — сверяем без хвостового слэша (RFC 9207).
        if issuer is not None and issuer.rstrip("/") != flow.issuer.rstrip("/"):
            raise McpOAuthFlowNotFoundError(
                f"OAuth flow state={state} issuer mismatch"
            )

        client = await self._clients.get(flow.connection_id)
        if client is None:
            raise McpOAuthClientMissingError(
                f"OAuth client for connection {flow.connection_id} missing"
            )

        token = await self._token_client.exchange_code(
            token_endpoint=flow.token_endpoint,
            code=code,
            code_verifier=flow.code_verifier,
            redirect_uri=flow.redirect_uri,
            resource=flow.resource,
            client=client,
        )

        await self._tokens.upsert(
            user_id,
            flow.connection_id,
            access_token=token.access_token,
            refresh_token=token.refresh_token,
            expires_at=_expiry_from(token.expires_in),
            scope=token.scope or flow.scope,
            token_endpoint=flow.token_endpoint,
        )
        return await self._connections.get_by_id(flow.connection_id)

    async def disconnect(self, user_id: UUID, connection_id: UUID) -> None:
        """Удаляет токены (user, connection). Идемпотентно."""
        await self._tokens.delete(user_id, connection_id)

    async def fresh_access_token(
        self, user_id: UUID, connection_id: UUID
    ) -> str | None:
        """Возвращает живой access-токен, обновляя по refresh при необходимости."""
        record = await self._tokens.get(user_id, connection_id)
        if record is None:
            return None
        if _access_alive(record):
            return record.access_token
        if record.refresh_token is None:
            return None

        async with self._lock_for(user_id, connection_id):
            record = await self._tokens.get(user_id, connection_id)
            if record is None:
                return None
            if _access_alive(record):
                return record.access_token  # сосед уже обновил под этой же блокировкой
            if record.refresh_token is None:
                return None
            return await self._refresh_locked(user_id, connection_id, record)

    async def status_for(
        self, user_id: UUID, connection_ids: list[UUID]
    ) -> dict[UUID, McpOAuthStatus]:
        """Статус OAuth-подключения по каждому connection_id для UI."""
        records = await self._tokens.list_for_user(user_id)
        by_connection = {record.connection_id: record for record in records}
        result: dict[UUID, McpOAuthStatus] = {}
        for connection_id in connection_ids:
            record = by_connection.get(connection_id)
            if record is None:
                result[connection_id] = "not_connected"
            elif record.refresh_failed_at is not None or (
                # протух без refresh_token — обновить нечем
                record.refresh_token is None and not _access_alive(record)
            ):
                result[connection_id] = "expired"
            else:
                result[connection_id] = "connected"
        return result

    async def upsert_preregistered_client(
        self, connection_id: UUID, *, client_id: str, client_secret: str | None
    ) -> McpOAuthClientRecord:
        """Пишет предрегистрированного OAuth-клиента (креды из admin-формы подключения)."""
        # Метод аутентификации по наличию секрета: секрет есть → post, нет → public.
        method = "client_secret_post" if client_secret else "none"
        return await self._clients.upsert(
            connection_id,
            client_id=client_id,
            client_secret=client_secret,
            token_endpoint_auth_method=method,
            source="preregistered",
        )

    async def get_client(self, connection_id: UUID) -> McpOAuthClientRecord | None:
        """Возвращает OAuth-клиента подключения или None (фасад для management-слоя)."""
        return await self._clients.get(connection_id)

    async def get_clients(
        self, connection_ids: list[UUID]
    ) -> dict[UUID, McpOAuthClientRecord]:
        """Батч OAuth-клиентов по connection_id; отсутствующие в карту не попадают."""
        if not connection_ids:
            return {}
        records = await asyncio.gather(
            *(self._clients.get(connection_id) for connection_id in connection_ids)
        )
        return {
            record.connection_id: record for record in records if record is not None
        }

    async def _refresh_locked(
        self, user_id: UUID, connection_id: UUID, record: McpOAuthTokenRecord
    ) -> str | None:
        """Обновляет токены под уже взятой блокировкой; CAS-запись против гонки."""
        client = await self._clients.get(connection_id)
        if client is None:
            return None
        connection = await self._connections.get_by_id(connection_id)
        resource = resource_url_from_server_url(connection.url)

        expected_refresh = record.refresh_token
        if expected_refresh is None:
            return None  # вызывающий уже отфильтровал, ветка — защита от рассинхрона
        try:
            token = await self._token_client.refresh(
                token_endpoint=record.token_endpoint,
                refresh_token=expected_refresh,
                resource=resource,
                client=client,
            )
        except McpOAuthRefreshRejectedError:
            await self._tokens.mark_refresh_failed(
                user_id, connection_id, expected_refresh_token=expected_refresh
            )
            return None
        except McpOAuthExchangeError:
            return None  # сетевой/прочий сбой — без пометки, повторим позже

        updated = await self._tokens.update_tokens_if_refresh_matches(
            user_id,
            connection_id,
            access_token=token.access_token,
            # ответ без refresh_token → старый сохраняется (Google отдаёт его только
            # на первом consent; затирание NULL'ом убило бы будущие refresh)
            refresh_token=token.refresh_token or expected_refresh,
            expires_at=_expiry_from(token.expires_in),
            scope=token.scope or record.scope,
            expected_refresh_token=expected_refresh,
        )
        if updated:
            return token.access_token

        refreshed = await self._tokens.get(user_id, connection_id)
        return refreshed.access_token if refreshed is not None else None

    async def _resolve_client(
        self, connection_id: UUID, info: AuthorizationServerInfo
    ) -> McpOAuthClientRecord:
        """Возвращает клиента: запись из БД, иначе DCR, иначе ClientMissing."""
        existing = await self._clients.get(connection_id)
        if existing is not None:
            return existing
        if info.registration_endpoint is None:
            raise McpOAuthClientMissingError(
                f"No OAuth client for connection {connection_id} and DCR unavailable"
            )
        return await self._register_client(
            connection_id,
            registration_endpoint=info.registration_endpoint,
            scopes_supported=info.scopes_supported,
        )

    async def _register_client(
        self,
        connection_id: UUID,
        *,
        registration_endpoint: str,
        scopes_supported: list[str] | None,
    ) -> McpOAuthClientRecord:
        """Регистрирует клиента (DCR) и пишет запись с валидным методом аутентификации."""
        registered = await self._token_client.register_client(
            registration_endpoint=registration_endpoint,
            redirect_uri=self._redirect_uri,
            scopes=scopes_supported,
        )
        if registered.client_id is None:
            raise McpOAuthRegistrationError(
                f"DCR for connection {connection_id} returned no client_id"
            )
        method = registered.token_endpoint_auth_method or (
            "client_secret_post" if registered.client_secret else "none"
        )
        if method not in _VALID_AUTH_METHODS:
            raise McpOAuthRegistrationError(
                f"DCR returned unsupported token_endpoint_auth_method '{method}'"
            )
        return await self._clients.upsert(
            connection_id,
            client_id=registered.client_id,
            client_secret=registered.client_secret,
            token_endpoint_auth_method=method,
            source="dcr",
            client_secret_expires_at=_dcr_secret_expiry(
                registered.client_secret_expires_at
            ),
        )

    async def _is_visible(
        self, user_id: UUID, connection: McpConnectionRecord
    ) -> bool:
        """Видимость как в резолве списка: public ∪ наличие подписки юзера."""
        if connection.is_public:
            return True
        subscription = await self._subscriptions.get(user_id, connection.connection_id)
        return subscription is not None

    def _lock_for(self, user_id: UUID, connection_id: UUID) -> asyncio.Lock:
        """Get-or-create блокировки per (user, connection). Атомарно в одном loop."""
        key = (user_id, connection_id)
        lock = self._refresh_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._refresh_locks[key] = lock
        return lock


def _build_authorization_url(
    *,
    authorization_endpoint: str,
    client_id: str,
    redirect_uri: str,
    state: str,
    code_challenge: str,
    resource: str,
    scope: str | None,
) -> str:
    """Собирает authorization URL (RFC 6749 + PKCE + RFC 8707 resource).

    Дубль приватной сборки из mcp.client.auth.oauth2 (в SDK метод недоступен извне) —
    осознанный: рассинхрон с будущими версиями SDK ловит тест на состав URL.
    access_type=offline и prompt=consent нужны Google для выдачи refresh_token;
    сторонние AS обязаны игнорировать неизвестные параметры (RFC 6749 §3.1).
    """
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": _PKCE_METHOD,
        "resource": resource,
        "access_type": "offline",
        "prompt": "consent",
    }
    if scope:
        params["scope"] = scope
    return f"{authorization_endpoint}?{urlencode(params)}"


def _require_pkce_s256(info: AuthorizationServerInfo) -> None:
    """MUST-проверка: если AS перечислил методы PKCE, среди них обязан быть S256."""
    methods = info.code_challenge_methods_supported
    if methods is not None and _PKCE_METHOD not in methods:
        raise McpOAuthDiscoveryError(
            f"Authorization server does not advertise {_PKCE_METHOD} PKCE support"
        )


def _select_scope(info: AuthorizationServerInfo) -> str | None:
    """scope по приоритету SDK: WWW-Authenticate hint → scopes_supported → без scope."""
    if info.scope_hint is not None:
        return info.scope_hint
    if info.scopes_supported:
        return " ".join(info.scopes_supported)
    return None


def _access_alive(record: McpOAuthTokenRecord) -> bool:
    """Жив ли access: expires_at=None считаем живым, иначе со скью 60 с."""
    if record.expires_at is None:
        return True
    return datetime.now(UTC) + timedelta(seconds=_ACCESS_SKEW_S) < record.expires_at


def _expiry_from(expires_in: int | None) -> datetime | None:
    """Момент протухания access из expires_in (сек); None → None."""
    if expires_in is None:
        return None
    return datetime.now(UTC) + timedelta(seconds=expires_in)


def _dcr_secret_expiry(client_secret_expires_at: int | None) -> datetime | None:
    """Unix-время протухания секрета из DCR → datetime; 0/None = бессрочный (RFC 7591)."""
    if not client_secret_expires_at:
        return None
    return datetime.fromtimestamp(client_secret_expires_at, UTC)
