"""Контракт MCP-management API: guards (admin/session/SSRF) + error-mapping + формы.

Stub-based (паттерн test_users_api.py): sync TestClient, без реальной БД. Сервис —
РЕАЛЬНЫЙ McpManagementService поверх стаб-репозиториев, чтобы проверить связку
router → service-инвариант → exception-handler.
"""

from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from bestfiend.app.http import create_app
from bestfiend.control_plane.auth.errors import InvalidSessionError
from bestfiend.control_plane.mcp.errors import McpConnectionNotFoundError
from bestfiend.control_plane.mcp.models import (
    McpConnectionRecord,
    McpServerWithSubscription,
)
from bestfiend.control_plane.mcp.oauth.models import (
    McpOAuthClientRecord,
    McpOAuthStatus,
)
from bestfiend.control_plane.mcp.service import McpManagementService
from bestfiend.control_plane.settings import AuthSettings
from bestfiend.control_plane.users.models import UserProfile, UserRole, UserStatus
from bestfiend.mcp.settings import McpDiscoverySettings


_AUTH_COOKIE_NAME = "bestfiend_session"
_NOW = datetime.now(UTC)


def _make_profile(
    *, role: UserRole = "user", status: UserStatus = "active"
) -> UserProfile:
    return UserProfile(
        user_id=uuid4(),
        role=role,
        status=status,
        telegram_chat_id=None,
        discord_user_id=None,
        login="u",
        timezone="Europe/Belgrade",
        created_at=_NOW,
        updated_at=None,
    )


def _conn(**overrides: Any) -> McpConnectionRecord:
    base: dict[str, Any] = {
        "connection_id": uuid4(),
        "name": "srv",
        "url": "https://example.com/mcp",
        "transport": "http_stream",
        "auth_type": "none",
        "is_public": False,
        "is_system": False,
        "timeout_s": 30.0,
        "created_at": _NOW,
        "updated_at": None,
    }
    base.update(overrides)
    return McpConnectionRecord(**base)


def _server_view(connection_id: UUID) -> McpServerWithSubscription:
    return McpServerWithSubscription(
        connection_id=connection_id,
        name="srv",
        url="https://example.com/mcp",
        transport="http_stream",
        auth_type="none",
        is_public=True,
        is_system=False,
        timeout_s=30.0,
        has_subscription=False,
        sub_enabled=None,
        sub_auth_token=None,
        sub_disabled_tools=None,
        sub_timeout_s=None,
        sub_created_at=None,
    )


class _ConnRepo:
    def __init__(self, record: McpConnectionRecord | None = None) -> None:
        self._record = record

    async def list_all(self) -> list[McpConnectionRecord]:
        return [self._record] if self._record is not None else []

    async def get_by_id(self, connection_id: Any) -> McpConnectionRecord:
        if self._record is None:
            raise McpConnectionNotFoundError(f"id={connection_id} not found")
        return self._record

    async def create(self, **fields: Any) -> McpConnectionRecord:
        return _conn(**fields)

    async def update(self, connection_id: Any, **fields: Any) -> McpConnectionRecord:
        return _conn(connection_id=connection_id, **fields)

    async def delete(self, connection_id: Any) -> None:
        return None


class _SubRepo:
    def __init__(self, visible: list[McpServerWithSubscription] | None = None) -> None:
        self._visible = visible or []

    async def list_visible_for_user(
        self, user_id: Any
    ) -> list[McpServerWithSubscription]:
        return self._visible

    async def get(self, user_id: Any, connection_id: Any) -> Any:
        return None

    async def upsert(self, user_id: Any, connection_id: Any, **fields: Any) -> Any:
        return None

    async def delete(self, user_id: Any, connection_id: Any) -> None:
        return None


def _oauth_client(connection_id: UUID) -> McpOAuthClientRecord:
    return McpOAuthClientRecord(
        connection_id=connection_id,
        client_id="cid-123",
        client_secret="secret-must-not-leak",
        token_endpoint_auth_method="client_secret_post",
        source="preregistered",
        client_secret_expires_at=None,
        created_at=_NOW,
        updated_at=None,
    )


class _OAuthServiceStub:
    """Стуб McpOAuthService: отдаёт заранее заданных клиентов и статусы."""

    def __init__(
        self,
        clients: dict[UUID, McpOAuthClientRecord] | None = None,
        statuses: dict[UUID, McpOAuthStatus] | None = None,
    ) -> None:
        self._clients = clients or {}
        self._statuses = statuses or {}

    async def get_clients(
        self, connection_ids: list[UUID]
    ) -> dict[UUID, McpOAuthClientRecord]:
        return {
            cid: self._clients[cid] for cid in connection_ids if cid in self._clients
        }

    async def get_client(self, connection_id: UUID) -> McpOAuthClientRecord | None:
        return self._clients.get(connection_id)

    async def upsert_preregistered_client(
        self, connection_id: UUID, *, client_id: str, client_secret: str | None
    ) -> McpOAuthClientRecord:
        record = McpOAuthClientRecord(
            connection_id=connection_id,
            client_id=client_id,
            client_secret=client_secret,
            token_endpoint_auth_method="client_secret_post"
            if client_secret
            else "none",
            source="preregistered",
            client_secret_expires_at=None,
            created_at=_NOW,
            updated_at=None,
        )
        self._clients[connection_id] = record
        return record

    async def status_for(
        self, user_id: Any, connection_ids: list[UUID]
    ) -> dict[UUID, McpOAuthStatus]:
        return {cid: self._statuses[cid] for cid in connection_ids if cid in self._statuses}

    async def fresh_access_token(
        self, user_id: Any, connection_id: Any
    ) -> str | None:
        return None


class _AuthServiceStub:
    def __init__(self) -> None:
        self._sessions: dict[UUID, UserProfile] = {}

    def seed_session(self, profile: UserProfile) -> str:
        session_id = uuid4()
        self._sessions[session_id] = profile
        return str(session_id)

    async def resolve_session(self, session_id: UUID) -> UserProfile:
        profile = self._sessions.get(session_id)
        if profile is None:
            raise InvalidSessionError(f"session_id={session_id} not found")
        return profile


class _RuntimeStub:
    def __init__(
        self, mcp_service: McpManagementService, auth: _AuthServiceStub
    ) -> None:
        self.mcp_management_service = mcp_service
        self.auth_service = auth
        self.auth_settings = AuthSettings(  # pyright: ignore[reportCallIssue]
            bcrypt_cost=4,
            binding_code_ttl_s=600,
            session_ttl_s=86400,
            cookie_name=_AUTH_COOKIE_NAME,
            cookie_secure=False,
        )


def _service(
    conn: _ConnRepo | None = None,
    sub: _SubRepo | None = None,
    oauth: _OAuthServiceStub | None = None,
) -> McpManagementService:
    return McpManagementService(
        connection_repository=cast(Any, conn or _ConnRepo()),
        subscription_repository=cast(Any, sub or _SubRepo()),
        oauth_service=cast(Any, oauth or _OAuthServiceStub()),
        discovery_settings=McpDiscoverySettings(),
    )


def _client(service: McpManagementService, auth: _AuthServiceStub) -> TestClient:
    return TestClient(create_app(cast(Any, _RuntimeStub(service, auth))))


def _admin_client(service: McpManagementService) -> tuple[TestClient, _AuthServiceStub]:
    auth = _AuthServiceStub()
    cookie = auth.seed_session(_make_profile(role="admin"))
    client = _client(service, auth)
    client.cookies.set(_AUTH_COOKIE_NAME, cookie)
    return client, auth


def _user_client(service: McpManagementService) -> tuple[TestClient, _AuthServiceStub]:
    auth = _AuthServiceStub()
    cookie = auth.seed_session(_make_profile(role="user"))
    client = _client(service, auth)
    client.cookies.set(_AUTH_COOKIE_NAME, cookie)
    return client, auth


# ─────────── connections (admin-guard) ───────────


def test_list_connections_admin_200() -> None:
    client, _ = _admin_client(_service(_ConnRepo(_conn())))
    with client:
        response = client.get("/mcp/connections")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["name"] == "srv"


def test_list_connections_oauth_view_exposes_client_id_not_secret() -> None:
    connection_id = uuid4()
    conn = _conn(connection_id=connection_id, auth_type="oauth", is_public=True)
    oauth = _OAuthServiceStub(clients={connection_id: _oauth_client(connection_id)})
    client, _ = _admin_client(_service(_ConnRepo(conn), oauth=oauth))
    with client:
        response = client.get("/mcp/connections")
    assert response.status_code == 200
    row = response.json()[0]
    assert row["oauth_client_id"] == "cid-123"
    assert row["oauth_client_source"] == "preregistered"
    # client_secret не отдаётся наружу ни ключом, ни значением.
    assert "client_secret" not in row
    assert "secret-must-not-leak" not in response.text


def test_list_connections_non_admin_403() -> None:
    client, _ = _user_client(_service())
    with client:
        response = client.get("/mcp/connections")
    assert response.status_code == 403
    assert response.json()["error_code"] == "AUTH_FORBIDDEN"


def test_list_connections_no_cookie_401() -> None:
    client = _client(_service(), _AuthServiceStub())
    with client:
        response = client.get("/mcp/connections")
    assert response.status_code == 401
    assert response.json()["error_code"] == "AUTH_INVALID_SESSION"


def test_create_connection_admin_200() -> None:
    client, _ = _admin_client(_service(_ConnRepo()))
    with client:
        response = client.post(
            "/mcp/connections",
            json={"name": "ws", "url": "https://ws/mcp"},
        )
    assert response.status_code == 200
    assert response.json()["name"] == "ws"


def test_create_connection_carries_parallel_flag() -> None:
    client, _ = _admin_client(_service(_ConnRepo()))
    with client:
        response = client.post(
            "/mcp/connections",
            json={
                "name": "seq",
                "url": "https://seq/mcp",
                "supports_parallel_tool_calls": False,
            },
        )
    assert response.status_code == 200
    assert response.json()["supports_parallel_tool_calls"] is False


def test_create_public_bearer_maps_to_400_validation() -> None:
    # Реальный сервис-инвариант public⇒none → McpValidationError → handler → 400.
    client, _ = _admin_client(_service(_ConnRepo()))
    with client:
        response = client.post(
            "/mcp/connections",
            json={
                "name": "ws",
                "url": "https://ws/mcp",
                "is_public": True,
                "auth_type": "bearer",
            },
        )
    assert response.status_code == 400
    assert response.json()["error_code"] == "MCP_VALIDATION"


def test_delete_system_connection_maps_to_409() -> None:
    client, _ = _admin_client(_service(_ConnRepo(_conn(is_system=True))))
    with client:
        response = client.delete(f"/mcp/connections/{uuid4()}")
    assert response.status_code == 409
    assert response.json()["error_code"] == "MCP_SYSTEM_PROTECTED"


# ─────────── subscriptions (session-guard) ───────────


def test_my_servers_session_200() -> None:
    client, _ = _user_client(_service(sub=_SubRepo([_server_view(uuid4())])))
    with client:
        response = client.get("/mcp/my-servers")
    assert response.status_code == 200
    assert len(response.json()) == 1


def test_upsert_subscription_returns_server_view() -> None:
    connection_id = uuid4()
    sub = _SubRepo([_server_view(connection_id)])
    client, _ = _user_client(_service(sub=sub))
    with client:
        response = client.put(
            f"/mcp/subscriptions/{connection_id}",
            json={"enabled": True, "timeout_s": 15.0},
        )
    assert response.status_code == 200
    assert response.json()["connection_id"] == str(connection_id)


# ─────────── preview (SSRF-guard) ───────────


def test_preview_adhoc_non_admin_403() -> None:
    client, _ = _user_client(_service())
    with client:
        response = client.post(
            "/mcp/discover-preview",
            json={"url": "https://evil/mcp"},
        )
    assert response.status_code == 403
    assert response.json()["error_code"] == "AUTH_FORBIDDEN"
