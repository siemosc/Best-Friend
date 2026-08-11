"""Контракт OAuth-тракта MCP API: start (JSON), browser callback (302), disconnect.

Stub-based (паттерн test_router.py): sync TestClient, McpOAuthService заменён стубом,
проверяем связку router → стуб → JSON/redirect/exception-handler. Редиректы не
разворачиваем (follow_redirects=False) — сверяем Location.
"""

from datetime import UTC, datetime
from typing import Any, cast
from urllib.parse import urlencode
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from bestfiend.app.http import create_app
from bestfiend.control_plane.auth.errors import InvalidSessionError
from bestfiend.control_plane.mcp.models import McpConnectionRecord
from bestfiend.control_plane.mcp.oauth.errors import (
    McpOAuthDiscoveryError,
    McpOAuthError,
    McpOAuthFlowNotFoundError,
)
from bestfiend.control_plane.settings import AuthSettings
from bestfiend.control_plane.users.models import UserProfile, UserRole, UserStatus


_AUTH_COOKIE_NAME = "bestfiend_session"
_BASE_URL = "http://localhost:5173"
_NOW = datetime.now(UTC)


def _make_profile(role: UserRole = "user", status: UserStatus = "active") -> UserProfile:
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


def _connection(name: str) -> McpConnectionRecord:
    return McpConnectionRecord(
        connection_id=uuid4(),
        name=name,
        url="https://example.com/mcp",
        transport="http_stream",
        auth_type="oauth",
        is_public=True,
        is_system=False,
        timeout_s=30.0,
        created_at=_NOW,
        updated_at=None,
    )


class _OAuthServiceStub:
    """Стуб McpOAuthService: программируемые URL/ошибки/connection на flow-методах."""

    def __init__(
        self,
        *,
        authorization_url: str = "https://as.example/authorize?client_id=x",
        start_error: McpOAuthError | None = None,
        complete_error: McpOAuthError | None = None,
        connection: McpConnectionRecord | None = None,
    ) -> None:
        self._authorization_url = authorization_url
        self._start_error = start_error
        self._complete_error = complete_error
        self._connection = connection or _connection("srv")
        self.disconnect_calls: list[tuple[UUID, UUID]] = []

    async def start_flow(self, user_id: UUID, connection_id: UUID) -> str:
        if self._start_error is not None:
            raise self._start_error
        return self._authorization_url

    async def complete_flow(
        self, user_id: UUID, state: str, code: str, issuer: str | None
    ) -> McpConnectionRecord:
        if self._complete_error is not None:
            raise self._complete_error
        return self._connection

    async def disconnect(self, user_id: UUID, connection_id: UUID) -> None:
        self.disconnect_calls.append((user_id, connection_id))


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
    def __init__(self, oauth: _OAuthServiceStub, auth: _AuthServiceStub) -> None:
        self.mcp_oauth_service = oauth
        self.public_base_url = _BASE_URL
        self.auth_service = auth
        self.auth_settings = AuthSettings(  # pyright: ignore[reportCallIssue]
            bcrypt_cost=4,
            binding_code_ttl_s=600,
            session_ttl_s=86400,
            cookie_name=_AUTH_COOKIE_NAME,
            cookie_secure=False,
        )


def _client(oauth: _OAuthServiceStub, auth: _AuthServiceStub) -> TestClient:
    return TestClient(create_app(cast(Any, _RuntimeStub(oauth, auth))))


def _session_client(oauth: _OAuthServiceStub) -> TestClient:
    auth = _AuthServiceStub()
    cookie = auth.seed_session(_make_profile())
    client = _client(oauth, auth)
    client.cookies.set(_AUTH_COOKIE_NAME, cookie)
    return client


# ─────────── start (session-guard, JSON) ───────────


def test_start_returns_authorization_url() -> None:
    oauth = _OAuthServiceStub(authorization_url="https://as/authorize?state=abc")
    client = _session_client(oauth)
    with client:
        response = client.post(f"/mcp/subscriptions/{uuid4()}/oauth/start")
    assert response.status_code == 200
    assert response.json()["authorization_url"] == "https://as/authorize?state=abc"


def test_start_domain_error_maps_to_json_error_code() -> None:
    oauth = _OAuthServiceStub(
        start_error=McpOAuthDiscoveryError("AS metadata unavailable")
    )
    client = _session_client(oauth)
    with client:
        response = client.post(f"/mcp/subscriptions/{uuid4()}/oauth/start")
    assert response.status_code == 502
    assert response.json()["error_code"] == "mcp_oauth_discovery_failed"


def test_start_no_session_401() -> None:
    client = _client(_OAuthServiceStub(), _AuthServiceStub())
    with client:
        response = client.post(f"/mcp/subscriptions/{uuid4()}/oauth/start")
    assert response.status_code == 401


# ─────────── callback (browser redirects) ───────────


def test_callback_success_redirects_with_encoded_name() -> None:
    oauth = _OAuthServiceStub(connection=_connection("My Server"))
    client = _session_client(oauth)
    with client:
        response = client.get(
            "/mcp/oauth/callback",
            params={"state": "st", "code": "cd"},
            follow_redirects=False,
        )
    assert response.status_code == 302
    location = response.headers["location"]
    assert location == f"{_BASE_URL}/mcp?{urlencode({'oauth_connected': 'My Server'})}"
    assert "oauth_connected=My+Server" in location


def test_callback_as_error_redirects_with_oauth_error() -> None:
    client = _session_client(_OAuthServiceStub())
    with client:
        response = client.get(
            "/mcp/oauth/callback",
            params={"state": "st", "error": "access_denied"},
            follow_redirects=False,
        )
    assert response.status_code == 302
    assert response.headers["location"] == f"{_BASE_URL}/mcp?oauth_error=access_denied"


def test_callback_bad_state_redirects_with_flow_expired() -> None:
    oauth = _OAuthServiceStub(
        complete_error=McpOAuthFlowNotFoundError("state unknown")
    )
    client = _session_client(oauth)
    with client:
        response = client.get(
            "/mcp/oauth/callback",
            params={"state": "st", "code": "cd"},
            follow_redirects=False,
        )
    assert response.status_code == 302
    assert (
        response.headers["location"]
        == f"{_BASE_URL}/mcp?oauth_error=mcp_oauth_flow_expired"
    )


def test_callback_no_session_redirects_to_login() -> None:
    client = _client(_OAuthServiceStub(), _AuthServiceStub())
    with client:
        response = client.get(
            "/mcp/oauth/callback",
            params={"state": "st", "code": "cd"},
            follow_redirects=False,
        )
    assert response.status_code == 302
    assert response.headers["location"] == f"{_BASE_URL}/login"


# ─────────── disconnect (session-guard) ───────────


def test_disconnect_returns_204() -> None:
    oauth = _OAuthServiceStub()
    client = _session_client(oauth)
    with client:
        response = client.delete(f"/mcp/subscriptions/{uuid4()}/oauth")
    assert response.status_code == 204
    assert len(oauth.disconnect_calls) == 1
