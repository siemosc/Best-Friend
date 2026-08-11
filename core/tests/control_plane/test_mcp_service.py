"""McpManagementService: инвариант public⇒none, guard is_system, preview-видимость.

Юнит на стабах репозиториев; discover_servers замокан (L2 проверяется отдельно).
"""

from datetime import UTC, datetime
from typing import Any, cast
from uuid import uuid4

import pytest

from bestfiend.control_plane.mcp.errors import (
    McpConnectionNotFoundError,
    McpSystemConnectionError,
    McpValidationError,
)
from bestfiend.control_plane.mcp.models import (
    McpConnectionRecord,
    McpServerWithSubscription,
)
from bestfiend.control_plane.mcp.oauth.models import (
    McpOAuthClientRecord,
    McpOAuthStatus,
)
from bestfiend.control_plane.mcp.service import McpManagementService
from bestfiend.mcp.contracts import ServerDiscovery
from bestfiend.mcp.settings import McpDiscoverySettings


_NOW = datetime.now(UTC)


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


class _ConnRepoStub:
    """Стаб McpConnectionRepository: get_by_id + перехват create/update/delete."""

    def __init__(self, record: McpConnectionRecord | None = None) -> None:
        self._record = record
        self.created: dict[str, Any] | None = None
        self.updated: tuple[Any, dict[str, Any]] | None = None
        self.deleted: Any = None

    async def get_by_id(self, connection_id: Any) -> McpConnectionRecord:
        if self._record is None:
            raise McpConnectionNotFoundError(f"id={connection_id} not found")
        return self._record

    async def create(self, **fields: Any) -> McpConnectionRecord:
        self.created = fields
        return _conn(**fields)

    async def update(self, connection_id: Any, **fields: Any) -> McpConnectionRecord:
        self.updated = (connection_id, fields)
        return _conn(connection_id=connection_id, **fields)

    async def delete(self, connection_id: Any) -> None:
        self.deleted = connection_id

    async def list_all(self) -> list[McpConnectionRecord]:
        return [self._record] if self._record is not None else []


class _SubRepoStub:
    """Стаб McpSubscriptionRepository: get (preview-видимость) + list_visible_for_user."""

    def __init__(
        self,
        subscription: Any = None,
        *,
        visible: list[McpServerWithSubscription] | None = None,
    ) -> None:
        self._sub = subscription
        self._visible = visible or []

    async def get(self, user_id: Any, connection_id: Any) -> Any:
        return self._sub

    async def list_visible_for_user(
        self, user_id: Any
    ) -> list[McpServerWithSubscription]:
        return self._visible


class _OAuthServiceStub:
    """Стаб McpOAuthService: фасады клиента, статусы и fresh-токен для management."""

    def __init__(
        self,
        *,
        client: McpOAuthClientRecord | None = None,
        clients: dict[Any, McpOAuthClientRecord] | None = None,
        statuses: dict[Any, McpOAuthStatus] | None = None,
        access: str | None = None,
    ) -> None:
        self._client = client
        self._clients = clients or {}
        self._statuses = statuses or {}
        self._access = access
        self.upserted: tuple[Any, str, str | None] | None = None

    async def upsert_preregistered_client(
        self, connection_id: Any, *, client_id: str, client_secret: str | None
    ) -> McpOAuthClientRecord:
        self.upserted = (connection_id, client_id, client_secret)
        method = "client_secret_post" if client_secret else "none"
        record = McpOAuthClientRecord(
            connection_id=connection_id,
            client_id=client_id,
            client_secret=client_secret,
            token_endpoint_auth_method=method,
            source="preregistered",
            created_at=_NOW,
        )
        self._client = record
        return record

    async def get_client(self, connection_id: Any) -> McpOAuthClientRecord | None:
        return self._client

    async def get_clients(
        self, connection_ids: list[Any]
    ) -> dict[Any, McpOAuthClientRecord]:
        return {
            cid: self._clients[cid] for cid in connection_ids if cid in self._clients
        }

    async def status_for(
        self, user_id: Any, connection_ids: list[Any]
    ) -> dict[Any, McpOAuthStatus]:
        return {cid: self._statuses.get(cid, "not_connected") for cid in connection_ids}

    async def fresh_access_token(
        self, user_id: Any, connection_id: Any
    ) -> str | None:
        return self._access


def _service(
    conn: _ConnRepoStub, sub: _SubRepoStub, oauth: _OAuthServiceStub | None = None
) -> McpManagementService:
    return McpManagementService(
        connection_repository=cast(Any, conn),
        subscription_repository=cast(Any, sub),
        oauth_service=cast(Any, oauth or _OAuthServiceStub()),
        discovery_settings=McpDiscoverySettings(),
    )


@pytest.fixture
def patch_discover(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Мокает discover_servers, перехватывая собранный ResolvedMcpServer."""
    captured: dict[str, Any] = {}

    async def _fake(servers: Any, settings: Any) -> list[ServerDiscovery]:
        captured["servers"] = servers
        return [
            ServerDiscovery(
                connection_id=servers[0].connection_id,
                name=servers[0].name,
                instructions="hi",
                tools=[],
                failure=None,
            )
        ]

    monkeypatch.setattr("bestfiend.control_plane.mcp.service.discover_servers", _fake)
    return captured


# ─────────── инвариант public ⇒ none ───────────


@pytest.mark.asyncio
async def test_create_public_bearer_rejected() -> None:
    conn = _ConnRepoStub()
    svc = _service(conn, _SubRepoStub())
    with pytest.raises(McpValidationError):
        await svc.create_connection(
            name="x",
            url="https://x/mcp",
            transport="http_stream",
            auth_type="bearer",
            is_public=True,
            timeout_s=30.0,
            supports_parallel_tool_calls=True,
        )
    assert conn.created is None  # инвариант сработал ДО репозитория


@pytest.mark.asyncio
async def test_create_public_none_ok() -> None:
    conn = _ConnRepoStub(_conn(is_public=True))
    svc = _service(conn, _SubRepoStub())
    await svc.create_connection(
        name="x",
        url="https://x/mcp",
        transport="http_stream",
        auth_type="none",
        is_public=True,
        timeout_s=30.0,
        supports_parallel_tool_calls=True,
    )
    assert conn.created is not None


@pytest.mark.asyncio
async def test_update_to_public_on_bearer_rejected() -> None:
    # Текущий сервер bearer; PATCH {is_public:true} без смены auth — инвариант на эффективных.
    conn_record = _conn(auth_type="bearer", is_public=False)
    conn = _ConnRepoStub(conn_record)
    svc = _service(conn, _SubRepoStub())
    with pytest.raises(McpValidationError):
        await svc.update_connection(conn_record.connection_id, {"is_public": True})
    assert conn.updated is None


@pytest.mark.asyncio
async def test_update_to_public_with_none_ok() -> None:
    conn_record = _conn(auth_type="bearer", is_public=False)
    conn = _ConnRepoStub(conn_record)
    svc = _service(conn, _SubRepoStub())
    await svc.update_connection(
        conn_record.connection_id, {"is_public": True, "auth_type": "none"}
    )
    assert conn.updated is not None


# ─────────── guard is_system ───────────


@pytest.mark.asyncio
async def test_delete_system_connection_forbidden() -> None:
    conn_record = _conn(is_system=True)
    conn = _ConnRepoStub(conn_record)
    svc = _service(conn, _SubRepoStub())
    with pytest.raises(McpSystemConnectionError):
        await svc.delete_connection(conn_record.connection_id)
    assert conn.deleted is None


@pytest.mark.asyncio
async def test_delete_regular_connection_ok() -> None:
    conn_record = _conn(is_system=False)
    conn = _ConnRepoStub(conn_record)
    svc = _service(conn, _SubRepoStub())
    await svc.delete_connection(conn_record.connection_id)
    assert conn.deleted == conn_record.connection_id


# ─────────── preview-видимость + SSRF-guard ───────────


@pytest.mark.asyncio
async def test_preview_by_id_public_no_token(patch_discover: dict[str, Any]) -> None:
    conn_record = _conn(is_public=True)
    svc = _service(_ConnRepoStub(conn_record), _SubRepoStub(None))
    result = await svc.discover_preview(
        is_admin=False,
        user_id=uuid4(),
        connection_id=conn_record.connection_id,
        url=None,
        auth_type=None,
        auth_token=None,
    )
    assert result.failure is None
    server = patch_discover["servers"][0]
    assert server.url == conn_record.url
    assert server.auth_token is None  # public none-auth → без токена


@pytest.mark.asyncio
async def test_preview_by_id_private_non_admin_hidden(
    patch_discover: dict[str, Any],
) -> None:
    conn_record = _conn(is_public=False)
    svc = _service(_ConnRepoStub(conn_record), _SubRepoStub(None))
    with pytest.raises(McpConnectionNotFoundError):
        await svc.discover_preview(
            is_admin=False,
            user_id=uuid4(),
            connection_id=conn_record.connection_id,
            url=None,
            auth_type=None,
            auth_token=None,
        )


@pytest.mark.asyncio
async def test_preview_by_id_private_admin_visible(
    patch_discover: dict[str, Any],
) -> None:
    conn_record = _conn(is_public=False)
    svc = _service(_ConnRepoStub(conn_record), _SubRepoStub(None))
    result = await svc.discover_preview(
        is_admin=True,
        user_id=uuid4(),
        connection_id=conn_record.connection_id,
        url=None,
        auth_type=None,
        auth_token=None,
    )
    assert result.name == conn_record.name


@pytest.mark.asyncio
async def test_preview_adhoc_non_admin_rejected(
    patch_discover: dict[str, Any],
) -> None:
    svc = _service(_ConnRepoStub(_conn()), _SubRepoStub(None))
    with pytest.raises(McpValidationError):
        await svc.discover_preview(
            is_admin=False,
            user_id=uuid4(),
            connection_id=None,
            url="https://x/mcp",
            auth_type="none",
            auth_token=None,
        )


@pytest.mark.asyncio
async def test_preview_adhoc_admin_builds_server(
    patch_discover: dict[str, Any],
) -> None:
    svc = _service(_ConnRepoStub(_conn()), _SubRepoStub(None))
    await svc.discover_preview(
        is_admin=True,
        user_id=uuid4(),
        connection_id=None,
        url="https://srv/mcp",
        auth_type="bearer",
        auth_token="tok",
    )
    server = patch_discover["servers"][0]
    assert server.url == "https://srv/mcp"
    assert server.auth_token == "tok"
    assert server.name == "preview"


# ─────────── OAuth: инвариант public⇒{none,oauth}, креды, композиция ───────────


def _oauth_client(connection_id: Any, **overrides: Any) -> McpOAuthClientRecord:
    base: dict[str, Any] = {
        "connection_id": connection_id,
        "client_id": "pre-client",
        "client_secret": "secret",
        "token_endpoint_auth_method": "client_secret_post",
        "source": "preregistered",
        "created_at": _NOW,
    }
    base.update(overrides)
    return McpOAuthClientRecord(**base)


def _visible(**overrides: Any) -> McpServerWithSubscription:
    base: dict[str, Any] = {
        "connection_id": uuid4(),
        "name": "srv",
        "url": "https://x/mcp",
        "transport": "http_stream",
        "auth_type": "none",
        "is_public": True,
        "is_system": False,
        "timeout_s": 30.0,
        "has_subscription": False,
        "sub_enabled": None,
        "sub_auth_token": None,
        "sub_disabled_tools": None,
        "sub_timeout_s": None,
        "sub_created_at": None,
    }
    base.update(overrides)
    return McpServerWithSubscription(**base)


@pytest.mark.asyncio
async def test_create_public_oauth_ok() -> None:
    conn = _ConnRepoStub(_conn(is_public=True, auth_type="oauth"))
    svc = _service(conn, _SubRepoStub())
    await svc.create_connection(
        name="x",
        url="https://x/mcp",
        transport="http_stream",
        auth_type="oauth",
        is_public=True,
        timeout_s=30.0,
        supports_parallel_tool_calls=True,
    )
    assert conn.created is not None  # public+oauth инвариант пропускает


@pytest.mark.asyncio
async def test_create_oauth_credentials_on_bearer_rejected() -> None:
    conn = _ConnRepoStub()
    svc = _service(conn, _SubRepoStub())
    with pytest.raises(McpValidationError):
        await svc.create_connection(
            name="x",
            url="https://x/mcp",
            transport="http_stream",
            auth_type="bearer",
            is_public=False,
            timeout_s=30.0,
            supports_parallel_tool_calls=True,
            oauth_client_id="cid",
        )
    assert conn.created is None  # валидация ДО репозитория


@pytest.mark.asyncio
async def test_create_oauth_with_credentials_upserts_client() -> None:
    conn = _ConnRepoStub(_conn(auth_type="oauth"))
    oauth = _OAuthServiceStub()
    svc = _service(conn, _SubRepoStub(), oauth)
    result = await svc.create_connection(
        name="x",
        url="https://x/mcp",
        transport="http_stream",
        auth_type="oauth",
        is_public=False,
        timeout_s=30.0,
        supports_parallel_tool_calls=True,
        oauth_client_id="pre-client",
        oauth_client_secret="secret",
    )
    assert oauth.upserted is not None
    assert oauth.upserted[1] == "pre-client"
    assert result.oauth_client is not None
    assert result.oauth_client.token_endpoint_auth_method == "client_secret_post"


@pytest.mark.asyncio
async def test_list_connections_composes_oauth_client() -> None:
    conn_record = _conn(auth_type="oauth")
    oauth = _OAuthServiceStub(
        clients={conn_record.connection_id: _oauth_client(conn_record.connection_id)}
    )
    svc = _service(_ConnRepoStub(conn_record), _SubRepoStub(), oauth)

    result = await svc.list_connections()

    assert len(result) == 1
    assert result[0].connection.connection_id == conn_record.connection_id
    assert result[0].oauth_client is not None


@pytest.mark.asyncio
async def test_list_connections_non_oauth_has_no_client() -> None:
    conn_record = _conn(auth_type="none")
    svc = _service(_ConnRepoStub(conn_record), _SubRepoStub(), _OAuthServiceStub())

    result = await svc.list_connections()

    assert result[0].oauth_client is None


@pytest.mark.asyncio
async def test_list_my_servers_fills_oauth_status() -> None:
    oauth_server = _visible(auth_type="oauth")
    plain_server = _visible(auth_type="none")
    oauth = _OAuthServiceStub(
        statuses={oauth_server.connection_id: "connected"}
    )
    svc = _service(
        _ConnRepoStub(),
        _SubRepoStub(visible=[oauth_server, plain_server]),
        oauth,
    )

    result = await svc.list_my_servers(uuid4())

    by_id = {s.connection_id: s for s in result}
    assert by_id[oauth_server.connection_id].oauth_status == "connected"
    assert by_id[plain_server.connection_id].oauth_status is None


@pytest.mark.asyncio
async def test_preview_oauth_uses_fresh_token(
    patch_discover: dict[str, Any],
) -> None:
    conn_record = _conn(is_public=True, auth_type="oauth")
    oauth = _OAuthServiceStub(access="live-access")
    svc = _service(_ConnRepoStub(conn_record), _SubRepoStub(None), oauth)

    await svc.discover_preview(
        is_admin=False,
        user_id=uuid4(),
        connection_id=conn_record.connection_id,
        url=None,
        auth_type=None,
        auth_token=None,
    )

    server = patch_discover["servers"][0]
    assert server.auth_token == "live-access"  # oauth → живой access, не sub-токен


# ─────────── none ⇒ без токена (легаси auth_token не течёт в заголовок) ───────────


@pytest.mark.asyncio
async def test_preview_by_id_none_auth_ignores_stale_subscription_token(
    patch_discover: dict[str, Any],
) -> None:
    from types import SimpleNamespace

    conn_record = _conn(auth_type="none", is_public=False)
    stale_sub = SimpleNamespace(auth_token="stale-token")  # остаток после смены auth_type
    svc = _service(_ConnRepoStub(conn_record), _SubRepoStub(stale_sub))
    await svc.discover_preview(
        is_admin=False,
        user_id=uuid4(),
        connection_id=conn_record.connection_id,
        url=None,
        auth_type=None,
        auth_token=None,
    )
    assert patch_discover["servers"][0].auth_token is None


@pytest.mark.asyncio
async def test_preview_adhoc_none_auth_drops_token(
    patch_discover: dict[str, Any],
) -> None:
    svc = _service(_ConnRepoStub(_conn()), _SubRepoStub(None))
    await svc.discover_preview(
        is_admin=True,
        user_id=uuid4(),
        connection_id=None,
        url="https://adhoc.example.com/mcp",
        auth_type=None,
        auth_token="should-not-leak",
    )
    assert patch_discover["servers"][0].auth_token is None


# ─────────── нормализация OAuth-кред ───────────


@pytest.mark.asyncio
async def test_create_secret_without_client_id_rejected() -> None:
    svc = _service(_ConnRepoStub(), _SubRepoStub(None))
    with pytest.raises(McpValidationError):
        await svc.create_connection(
            name="x",
            url="https://x/mcp",
            transport="http_stream",
            auth_type="oauth",
            is_public=False,
            timeout_s=30.0,
            supports_parallel_tool_calls=True,
            oauth_client_secret="orphan-secret",
        )


@pytest.mark.asyncio
async def test_create_blank_client_id_with_secret_rejected() -> None:
    """Пустая строка client_id из формы нормализуется в None → секрет-сирота."""
    svc = _service(_ConnRepoStub(), _SubRepoStub(None))
    with pytest.raises(McpValidationError):
        await svc.create_connection(
            name="x",
            url="https://x/mcp",
            transport="http_stream",
            auth_type="oauth",
            is_public=False,
            timeout_s=30.0,
            supports_parallel_tool_calls=True,
            oauth_client_id="   ",
            oauth_client_secret="secret",
        )


@pytest.mark.asyncio
async def test_create_credentials_trimmed_and_blank_secret_dropped() -> None:
    oauth = _OAuthServiceStub()
    svc = _service(_ConnRepoStub(), _SubRepoStub(None), oauth)
    await svc.create_connection(
        name="x",
        url="https://x/mcp",
        transport="http_stream",
        auth_type="oauth",
        is_public=False,
        timeout_s=30.0,
        supports_parallel_tool_calls=True,
        oauth_client_id="  pre-client  ",
        oauth_client_secret="   ",
    )
    assert oauth.upserted is not None
    _, upserted_id, upserted_secret = oauth.upserted
    assert upserted_id == "pre-client"
    assert upserted_secret is None
