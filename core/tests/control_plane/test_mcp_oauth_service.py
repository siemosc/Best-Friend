"""McpOAuthService: состав authorization URL, ветки клиента, refresh, статусы.

Юнит на стабах репозиториев и token_client; discover_authorization_server замокан.
Проверяем оркестрацию split-flow без сети и БД.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4

from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from pydantic import AnyUrl
import pytest

from bestfiend.control_plane.mcp.errors import (
    McpConnectionNotFoundError,
    McpValidationError,
)
from bestfiend.control_plane.mcp.models import McpConnectionRecord
from bestfiend.control_plane.mcp.oauth.discovery import AuthorizationServerInfo
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
    McpOAuthFlowRecord,
    McpOAuthTokenRecord,
)
from bestfiend.control_plane.mcp.oauth.service import McpOAuthService


_NOW = datetime.now(UTC)
_SERVER_URL = "https://mcp.example.com/mcp"
_RESOURCE = "https://mcp.example.com/mcp"
_REDIRECT_URI = "https://app.example.com/api/mcp/oauth/callback"
_ISSUER = "https://as.example.com"


# ─────────── фабрики записей ───────────


def _conn(connection_id: UUID, **overrides: Any) -> McpConnectionRecord:
    base: dict[str, Any] = {
        "connection_id": connection_id,
        "name": "srv",
        "url": _SERVER_URL,
        "transport": "http_stream",
        "auth_type": "oauth",
        "is_public": True,
        "is_system": False,
        "timeout_s": 30.0,
        "created_at": _NOW,
        "updated_at": None,
    }
    base.update(overrides)
    return McpConnectionRecord(**base)


def _client_record(connection_id: UUID, **overrides: Any) -> McpOAuthClientRecord:
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


def _info(**overrides: Any) -> AuthorizationServerInfo:
    base: dict[str, Any] = {
        "issuer": _ISSUER,
        "authorization_endpoint": "https://as.example.com/authorize",
        "token_endpoint": "https://as.example.com/token",
        "registration_endpoint": None,
        "scope_hint": "openid email",
        "scopes_supported": ["openid", "email", "profile"],
        "code_challenge_methods_supported": ["S256"],
    }
    base.update(overrides)
    return AuthorizationServerInfo(**base)


def _token(**overrides: Any) -> OAuthToken:
    base: dict[str, Any] = {
        "access_token": "access-1",
        "expires_in": 3600,
        "refresh_token": "refresh-1",
        "scope": "openid email",
    }
    base.update(overrides)
    return OAuthToken(**base)


def _token_record(
    user_id: UUID, connection_id: UUID, **overrides: Any
) -> McpOAuthTokenRecord:
    base: dict[str, Any] = {
        "user_id": user_id,
        "connection_id": connection_id,
        "access_token": "access-1",
        "refresh_token": "refresh-1",
        "expires_at": _NOW + timedelta(hours=1),
        "scope": "openid email",
        "token_endpoint": "https://as.example.com/token",
        "refresh_failed_at": None,
        "created_at": _NOW,
    }
    base.update(overrides)
    return McpOAuthTokenRecord(**base)


# ─────────── стабы репозиториев ───────────


class _ConnRepoStub:
    def __init__(self, conn: McpConnectionRecord) -> None:
        self._conn = conn

    async def get_by_id(self, connection_id: UUID) -> McpConnectionRecord:
        return self._conn


class _SubRepoStub:
    def __init__(self, subscription: Any = None) -> None:
        self._sub = subscription

    async def get(self, user_id: UUID, connection_id: UUID) -> Any:
        return self._sub


class _ClientRepoStub:
    def __init__(self, existing: McpOAuthClientRecord | None = None) -> None:
        self._record = existing
        self.upserted: dict[str, Any] | None = None

    async def get(self, connection_id: UUID) -> McpOAuthClientRecord | None:
        return self._record

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
        self.upserted = {
            "client_id": client_id,
            "token_endpoint_auth_method": token_endpoint_auth_method,
            "source": source,
        }
        record = _client_record(
            connection_id,
            client_id=client_id,
            client_secret=client_secret,
            token_endpoint_auth_method=token_endpoint_auth_method,
            source=source,
            client_secret_expires_at=client_secret_expires_at,
        )
        self._record = record
        return record


class _FlowRepoStub:
    def __init__(self) -> None:
        self.created: McpOAuthFlowRecord | None = None
        self.purge_calls = 0
        self._store: dict[str, McpOAuthFlowRecord] = {}

    def seed(self, flow: McpOAuthFlowRecord) -> None:
        self._store[flow.state] = flow

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
        record = McpOAuthFlowRecord(
            state=state,
            user_id=user_id,
            connection_id=connection_id,
            code_verifier=code_verifier,
            redirect_uri=redirect_uri,
            token_endpoint=token_endpoint,
            issuer=issuer,
            resource=resource,
            scope=scope,
            expires_at=expires_at,
            created_at=_NOW,
        )
        self.created = record
        self._store[state] = record
        return record

    async def take(self, state: str) -> McpOAuthFlowRecord | None:
        return self._store.pop(state, None)

    async def purge_expired(self) -> int:
        self.purge_calls += 1
        return 0


class _TokenRepoStub:
    def __init__(self) -> None:
        self._store: dict[tuple[UUID, UUID], McpOAuthTokenRecord] = {}
        self.upserts: list[McpOAuthTokenRecord] = []
        self.marked: list[tuple[UUID, UUID]] = []

    def seed(self, record: McpOAuthTokenRecord) -> None:
        self._store[(record.user_id, record.connection_id)] = record

    async def get(
        self, user_id: UUID, connection_id: UUID
    ) -> McpOAuthTokenRecord | None:
        return self._store.get((user_id, connection_id))

    async def list_for_user(self, user_id: UUID) -> list[McpOAuthTokenRecord]:
        return [r for (u, _), r in self._store.items() if u == user_id]

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
        record = _token_record(
            user_id,
            connection_id,
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=expires_at,
            scope=scope,
            token_endpoint=token_endpoint,
            refresh_failed_at=None,
        )
        self._store[(user_id, connection_id)] = record
        self.upserts.append(record)
        return record

    async def delete(self, user_id: UUID, connection_id: UUID) -> None:
        self._store.pop((user_id, connection_id), None)

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
        record = self._store.get((user_id, connection_id))
        if record is None or record.refresh_token != expected_refresh_token:
            return False
        self._store[(user_id, connection_id)] = record.model_copy(
            update={
                "access_token": access_token,
                "refresh_token": refresh_token,
                "expires_at": expires_at,
                "scope": scope,
                "refresh_failed_at": None,
            }
        )
        return True

    async def mark_refresh_failed(
        self, user_id: UUID, connection_id: UUID, *, expected_refresh_token: str
    ) -> bool:
        record = self._store.get((user_id, connection_id))
        if record is None or record.refresh_token != expected_refresh_token:
            return False
        self._store[(user_id, connection_id)] = record.model_copy(
            update={"refresh_failed_at": _NOW}
        )
        self.marked.append((user_id, connection_id))
        return True


class _TokenClientStub:
    def __init__(self) -> None:
        self.refresh_calls = 0
        self.register_calls = 0
        self.exchange_result: OAuthToken = _token()
        self.refresh_result: OAuthToken = _token(access_token="access-2")
        self.refresh_error: Exception | None = None
        self.register_result: OAuthClientInformationFull = OAuthClientInformationFull(
            client_id="dcr-client",
            client_secret="dcr-secret",
            token_endpoint_auth_method="client_secret_post",
            redirect_uris=[AnyUrl(_REDIRECT_URI)],
        )

    async def exchange_code(self, **kwargs: Any) -> OAuthToken:
        return self.exchange_result

    async def refresh(self, **kwargs: Any) -> OAuthToken:
        self.refresh_calls += 1
        await asyncio.sleep(0)
        if self.refresh_error is not None:
            raise self.refresh_error
        return self.refresh_result

    async def register_client(self, **kwargs: Any) -> OAuthClientInformationFull:
        self.register_calls += 1
        return self.register_result


def _service(
    *,
    conn: _ConnRepoStub,
    sub: _SubRepoStub | None = None,
    client: _ClientRepoStub | None = None,
    flow: _FlowRepoStub | None = None,
    token: _TokenRepoStub | Any = None,
    token_client: _TokenClientStub | None = None,
) -> McpOAuthService:
    return McpOAuthService(
        client_repository=cast(Any, client or _ClientRepoStub()),
        flow_repository=cast(Any, flow or _FlowRepoStub()),
        token_repository=cast(Any, token if token is not None else _TokenRepoStub()),
        connection_repository=cast(Any, conn),
        subscription_repository=cast(Any, sub or _SubRepoStub()),
        token_client=cast(Any, token_client or _TokenClientStub()),
        redirect_uri=_REDIRECT_URI,
    )


def _patch_discover(
    monkeypatch: pytest.MonkeyPatch, info: AuthorizationServerInfo
) -> None:
    async def _fake(server_url: str, *, timeout_s: float) -> AuthorizationServerInfo:
        return info

    monkeypatch.setattr(
        "bestfiend.control_plane.mcp.oauth.service.discover_authorization_server",
        _fake,
    )


# ─────────── start_flow: authorization URL ───────────


@pytest.mark.asyncio
async def test_authorization_url_composition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cid = uuid4()
    uid = uuid4()
    flow = _FlowRepoStub()
    _patch_discover(monkeypatch, _info())
    svc = _service(
        conn=_ConnRepoStub(_conn(cid)),
        client=_ClientRepoStub(_client_record(cid, client_id="pre-client")),
        flow=flow,
    )

    url = await svc.start_flow(uid, cid)

    split = urlsplit(url)
    assert f"{split.scheme}://{split.netloc}{split.path}" == (
        "https://as.example.com/authorize"
    )
    params = parse_qs(split.query)
    assert params["response_type"] == ["code"]
    assert params["client_id"] == ["pre-client"]
    assert params["redirect_uri"] == [_REDIRECT_URI]
    assert params["code_challenge_method"] == ["S256"]
    assert params["code_challenge"][0]
    assert params["resource"] == [_RESOURCE]
    assert params["access_type"] == ["offline"]
    assert params["prompt"] == ["consent"]
    assert params["scope"] == ["openid email"]  # scope_hint приоритетнее

    assert flow.created is not None
    assert params["state"] == [flow.created.state]
    assert flow.created.issuer == _ISSUER
    assert flow.created.token_endpoint == "https://as.example.com/token"
    assert flow.created.resource == _RESOURCE
    assert flow.created.scope == "openid email"
    assert flow.created.code_verifier
    assert flow.purge_calls == 1


@pytest.mark.asyncio
async def test_scope_falls_back_to_scopes_supported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cid = uuid4()
    _patch_discover(
        monkeypatch, _info(scope_hint=None, scopes_supported=["openid", "email"])
    )
    svc = _service(conn=_ConnRepoStub(_conn(cid)), client=_ClientRepoStub(_client_record(cid)))

    url = await svc.start_flow(uuid4(), cid)

    params = parse_qs(urlsplit(url).query)
    assert params["scope"] == ["openid email"]


@pytest.mark.asyncio
async def test_scope_omitted_when_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    cid = uuid4()
    _patch_discover(monkeypatch, _info(scope_hint=None, scopes_supported=None))
    svc = _service(conn=_ConnRepoStub(_conn(cid)), client=_ClientRepoStub(_client_record(cid)))

    url = await svc.start_flow(uuid4(), cid)

    assert "scope=" not in urlsplit(url).query


# ─────────── start_flow: ветки клиента ───────────


@pytest.mark.asyncio
async def test_start_flow_dcr_branch(monkeypatch: pytest.MonkeyPatch) -> None:
    cid = uuid4()
    client_repo = _ClientRepoStub(existing=None)
    token_client = _TokenClientStub()
    _patch_discover(
        monkeypatch, _info(registration_endpoint="https://as.example.com/register")
    )
    svc = _service(conn=_ConnRepoStub(_conn(cid)), client=client_repo, token_client=token_client)

    url = await svc.start_flow(uuid4(), cid)

    assert token_client.register_calls == 1
    assert client_repo.upserted is not None
    assert client_repo.upserted["source"] == "dcr"
    assert client_repo.upserted["token_endpoint_auth_method"] == "client_secret_post"
    params = parse_qs(urlsplit(url).query)
    assert params["client_id"] == ["dcr-client"]


@pytest.mark.asyncio
async def test_start_flow_preregistered_branch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cid = uuid4()
    client_repo = _ClientRepoStub(_client_record(cid, client_id="pre-client"))
    token_client = _TokenClientStub()
    _patch_discover(
        monkeypatch, _info(registration_endpoint="https://as.example.com/register")
    )
    svc = _service(conn=_ConnRepoStub(_conn(cid)), client=client_repo, token_client=token_client)

    await svc.start_flow(uuid4(), cid)

    assert token_client.register_calls == 0  # запись есть — DCR не вызывается
    assert client_repo.upserted is None


@pytest.mark.asyncio
async def test_start_flow_client_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    cid = uuid4()
    _patch_discover(monkeypatch, _info(registration_endpoint=None))
    svc = _service(conn=_ConnRepoStub(_conn(cid)), client=_ClientRepoStub(existing=None))

    with pytest.raises(McpOAuthClientMissingError):
        await svc.start_flow(uuid4(), cid)


@pytest.mark.asyncio
async def test_start_flow_dcr_unsupported_method(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cid = uuid4()
    token_client = _TokenClientStub()
    token_client.register_result = OAuthClientInformationFull(
        client_id="dcr-client",
        token_endpoint_auth_method="private_key_jwt",
        redirect_uris=[AnyUrl(_REDIRECT_URI)],
    )
    _patch_discover(
        monkeypatch, _info(registration_endpoint="https://as.example.com/register")
    )
    svc = _service(
        conn=_ConnRepoStub(_conn(cid)),
        client=_ClientRepoStub(existing=None),
        token_client=token_client,
    )

    with pytest.raises(McpOAuthRegistrationError):
        await svc.start_flow(uuid4(), cid)


# ─────────── start_flow: PKCE и видимость ───────────


@pytest.mark.asyncio
async def test_start_flow_requires_s256(monkeypatch: pytest.MonkeyPatch) -> None:
    cid = uuid4()
    _patch_discover(monkeypatch, _info(code_challenge_methods_supported=["plain"]))
    svc = _service(conn=_ConnRepoStub(_conn(cid)), client=_ClientRepoStub(_client_record(cid)))

    with pytest.raises(McpOAuthDiscoveryError):
        await svc.start_flow(uuid4(), cid)


@pytest.mark.asyncio
async def test_start_flow_allows_missing_pkce_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cid = uuid4()
    _patch_discover(monkeypatch, _info(code_challenge_methods_supported=None))
    svc = _service(conn=_ConnRepoStub(_conn(cid)), client=_ClientRepoStub(_client_record(cid)))

    url = await svc.start_flow(uuid4(), cid)
    assert url


@pytest.mark.asyncio
async def test_start_flow_non_oauth_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cid = uuid4()
    _patch_discover(monkeypatch, _info())
    svc = _service(conn=_ConnRepoStub(_conn(cid, auth_type="bearer")))

    with pytest.raises(McpValidationError):
        await svc.start_flow(uuid4(), cid)


@pytest.mark.asyncio
async def test_start_flow_invisible_private(monkeypatch: pytest.MonkeyPatch) -> None:
    cid = uuid4()
    _patch_discover(monkeypatch, _info())
    svc = _service(
        conn=_ConnRepoStub(_conn(cid, is_public=False)),
        sub=_SubRepoStub(subscription=None),  # нет подписки — не видно
        client=_ClientRepoStub(_client_record(cid)),
    )

    with pytest.raises(McpConnectionNotFoundError):
        await svc.start_flow(uuid4(), cid)


# ─────────── complete_flow ───────────


def _seed_flow(
    flow: _FlowRepoStub,
    *,
    state: str,
    user_id: UUID,
    connection_id: UUID,
    issuer: str = _ISSUER,
    expires_at: datetime | None = None,
) -> None:
    flow.seed(
        McpOAuthFlowRecord(
            state=state,
            user_id=user_id,
            connection_id=connection_id,
            code_verifier="verifier",
            redirect_uri=_REDIRECT_URI,
            token_endpoint="https://as.example.com/token",
            issuer=issuer,
            resource=_RESOURCE,
            scope="openid email",
            expires_at=expires_at or (_NOW + timedelta(minutes=5)),
            created_at=_NOW,
        )
    )


@pytest.mark.asyncio
async def test_complete_flow_happy() -> None:
    cid = uuid4()
    uid = uuid4()
    flow = _FlowRepoStub()
    _seed_flow(flow, state="st", user_id=uid, connection_id=cid)
    token_repo = _TokenRepoStub()
    conn = _conn(cid)
    svc = _service(
        conn=_ConnRepoStub(conn),
        client=_ClientRepoStub(_client_record(cid)),
        flow=flow,
        token=token_repo,
    )

    result = await svc.complete_flow(uid, "st", "code", _ISSUER)

    assert result.connection_id == cid
    assert len(token_repo.upserts) == 1
    assert token_repo.upserts[0].access_token == "access-1"


@pytest.mark.asyncio
async def test_complete_flow_unknown_state() -> None:
    svc = _service(conn=_ConnRepoStub(_conn(uuid4())), flow=_FlowRepoStub())
    with pytest.raises(McpOAuthFlowNotFoundError):
        await svc.complete_flow(uuid4(), "nope", "code", None)


@pytest.mark.asyncio
async def test_complete_flow_state_is_single_use() -> None:
    cid = uuid4()
    uid = uuid4()
    flow = _FlowRepoStub()
    _seed_flow(flow, state="st", user_id=uid, connection_id=cid)
    svc = _service(
        conn=_ConnRepoStub(_conn(cid)),
        client=_ClientRepoStub(_client_record(cid)),
        flow=flow,
        token=_TokenRepoStub(),
    )

    await svc.complete_flow(uid, "st", "code", _ISSUER)
    with pytest.raises(McpOAuthFlowNotFoundError):
        await svc.complete_flow(uid, "st", "code", _ISSUER)


@pytest.mark.asyncio
async def test_complete_flow_issuer_mismatch() -> None:
    cid = uuid4()
    uid = uuid4()
    flow = _FlowRepoStub()
    _seed_flow(flow, state="st", user_id=uid, connection_id=cid, issuer=_ISSUER)
    svc = _service(conn=_ConnRepoStub(_conn(cid)), flow=flow)

    with pytest.raises(McpOAuthFlowNotFoundError):
        await svc.complete_flow(uid, "st", "code", "https://evil.example.com")


@pytest.mark.asyncio
async def test_complete_flow_expired() -> None:
    cid = uuid4()
    uid = uuid4()
    flow = _FlowRepoStub()
    _seed_flow(
        flow,
        state="st",
        user_id=uid,
        connection_id=cid,
        expires_at=_NOW - timedelta(minutes=1),
    )
    svc = _service(conn=_ConnRepoStub(_conn(cid)), flow=flow)

    with pytest.raises(McpOAuthFlowNotFoundError):
        await svc.complete_flow(uid, "st", "code", _ISSUER)


@pytest.mark.asyncio
async def test_complete_flow_user_mismatch() -> None:
    cid = uuid4()
    flow = _FlowRepoStub()
    _seed_flow(flow, state="st", user_id=uuid4(), connection_id=cid)
    svc = _service(conn=_ConnRepoStub(_conn(cid)), flow=flow)

    with pytest.raises(McpOAuthFlowNotFoundError):
        await svc.complete_flow(uuid4(), "st", "code", _ISSUER)


# ─────────── fresh_access_token ───────────


@pytest.mark.asyncio
async def test_fresh_access_alive_no_refresh() -> None:
    cid, uid = uuid4(), uuid4()
    token_repo = _TokenRepoStub()
    token_repo.seed(_token_record(uid, cid, expires_at=_NOW + timedelta(hours=1)))
    token_client = _TokenClientStub()
    svc = _service(
        conn=_ConnRepoStub(_conn(cid)),
        client=_ClientRepoStub(_client_record(cid)),
        token=token_repo,
        token_client=token_client,
    )

    access = await svc.fresh_access_token(uid, cid)
    assert access == "access-1"
    assert token_client.refresh_calls == 0


@pytest.mark.asyncio
async def test_fresh_access_expired_refreshes() -> None:
    cid, uid = uuid4(), uuid4()
    token_repo = _TokenRepoStub()
    token_repo.seed(
        _token_record(uid, cid, expires_at=_NOW - timedelta(minutes=1))
    )
    token_client = _TokenClientStub()
    token_client.refresh_result = _token(
        access_token="access-2", refresh_token="refresh-2"
    )
    svc = _service(
        conn=_ConnRepoStub(_conn(cid)),
        client=_ClientRepoStub(_client_record(cid)),
        token=token_repo,
        token_client=token_client,
    )

    access = await svc.fresh_access_token(uid, cid)
    assert access == "access-2"
    assert token_client.refresh_calls == 1
    stored = await token_repo.get(uid, cid)
    assert stored is not None
    assert stored.refresh_token == "refresh-2"


@pytest.mark.asyncio
async def test_fresh_access_expired_no_refresh_token() -> None:
    cid, uid = uuid4(), uuid4()
    token_repo = _TokenRepoStub()
    token_repo.seed(
        _token_record(
            uid, cid, expires_at=_NOW - timedelta(minutes=1), refresh_token=None
        )
    )
    svc = _service(conn=_ConnRepoStub(_conn(cid)), token=token_repo)

    assert await svc.fresh_access_token(uid, cid) is None


@pytest.mark.asyncio
async def test_fresh_access_no_record() -> None:
    cid, uid = uuid4(), uuid4()
    svc = _service(conn=_ConnRepoStub(_conn(cid)), token=_TokenRepoStub())
    assert await svc.fresh_access_token(uid, cid) is None


@pytest.mark.asyncio
async def test_fresh_access_invalid_grant_marks_failed() -> None:
    cid, uid = uuid4(), uuid4()
    token_repo = _TokenRepoStub()
    token_repo.seed(_token_record(uid, cid, expires_at=_NOW - timedelta(minutes=1)))
    token_client = _TokenClientStub()
    token_client.refresh_error = McpOAuthRefreshRejectedError("invalid_grant")
    svc = _service(
        conn=_ConnRepoStub(_conn(cid)),
        client=_ClientRepoStub(_client_record(cid)),
        token=token_repo,
        token_client=token_client,
    )

    assert await svc.fresh_access_token(uid, cid) is None
    assert (uid, cid) in token_repo.marked
    stored = await token_repo.get(uid, cid)
    assert stored is not None
    assert stored.refresh_failed_at is not None


@pytest.mark.asyncio
async def test_fresh_access_network_failure_not_marked() -> None:
    cid, uid = uuid4(), uuid4()
    token_repo = _TokenRepoStub()
    token_repo.seed(_token_record(uid, cid, expires_at=_NOW - timedelta(minutes=1)))
    token_client = _TokenClientStub()
    token_client.refresh_error = McpOAuthExchangeError("network")
    svc = _service(
        conn=_ConnRepoStub(_conn(cid)),
        client=_ClientRepoStub(_client_record(cid)),
        token=token_repo,
        token_client=token_client,
    )

    assert await svc.fresh_access_token(uid, cid) is None
    assert (uid, cid) not in token_repo.marked


@pytest.mark.asyncio
async def test_fresh_access_preserves_refresh_token() -> None:
    cid, uid = uuid4(), uuid4()
    token_repo = _TokenRepoStub()
    token_repo.seed(
        _token_record(
            uid, cid, expires_at=_NOW - timedelta(minutes=1), refresh_token="old-r"
        )
    )
    token_client = _TokenClientStub()
    # ответ БЕЗ refresh_token → старый должен сохраниться
    token_client.refresh_result = _token(access_token="access-2", refresh_token=None)
    svc = _service(
        conn=_ConnRepoStub(_conn(cid)),
        client=_ClientRepoStub(_client_record(cid)),
        token=token_repo,
        token_client=token_client,
    )

    access = await svc.fresh_access_token(uid, cid)
    assert access == "access-2"
    stored = await token_repo.get(uid, cid)
    assert stored is not None
    assert stored.refresh_token == "old-r"


@pytest.mark.asyncio
async def test_fresh_access_concurrent_single_refresh() -> None:
    cid, uid = uuid4(), uuid4()
    token_repo = _TokenRepoStub()
    token_repo.seed(_token_record(uid, cid, expires_at=_NOW - timedelta(minutes=1)))
    token_client = _TokenClientStub()
    token_client.refresh_result = _token(
        access_token="access-2", refresh_token="refresh-2", expires_in=3600
    )
    svc = _service(
        conn=_ConnRepoStub(_conn(cid)),
        client=_ClientRepoStub(_client_record(cid)),
        token=token_repo,
        token_client=token_client,
    )

    results = await asyncio.gather(
        svc.fresh_access_token(uid, cid), svc.fresh_access_token(uid, cid)
    )

    assert results == ["access-2", "access-2"]
    assert token_client.refresh_calls == 1  # второй ждёт lock и берёт готовый access


class _CasConflictTokenRepo:
    """update всегда False (сосед обновил); третий get отдаёт свежую запись."""

    def __init__(
        self, expired: McpOAuthTokenRecord, sibling: McpOAuthTokenRecord
    ) -> None:
        self._expired = expired
        self._sibling = sibling
        self._get_count = 0

    async def get(
        self, user_id: UUID, connection_id: UUID
    ) -> McpOAuthTokenRecord | None:
        self._get_count += 1
        return self._expired if self._get_count <= 2 else self._sibling

    async def list_for_user(self, user_id: UUID) -> list[McpOAuthTokenRecord]:
        return [self._expired]

    async def update_tokens_if_refresh_matches(self, *a: Any, **k: Any) -> bool:
        return False

    async def mark_refresh_failed(self, *a: Any, **k: Any) -> bool:
        return False


@pytest.mark.asyncio
async def test_fresh_access_cas_conflict_rereads() -> None:
    cid, uid = uuid4(), uuid4()
    expired = _token_record(uid, cid, expires_at=_NOW - timedelta(minutes=1))
    sibling = _token_record(
        uid, cid, access_token="sibling-access", expires_at=_NOW + timedelta(hours=1)
    )
    token_repo = _CasConflictTokenRepo(expired, sibling)
    token_client = _TokenClientStub()
    svc = _service(
        conn=_ConnRepoStub(_conn(cid)),
        client=_ClientRepoStub(_client_record(cid)),
        token=token_repo,
        token_client=token_client,
    )

    access = await svc.fresh_access_token(uid, cid)
    assert access == "sibling-access"  # CAS не прошёл → перечитали запись соседа


# ─────────── disconnect / status_for ───────────


@pytest.mark.asyncio
async def test_disconnect_idempotent() -> None:
    cid, uid = uuid4(), uuid4()
    token_repo = _TokenRepoStub()
    token_repo.seed(_token_record(uid, cid))
    svc = _service(conn=_ConnRepoStub(_conn(cid)), token=token_repo)

    await svc.disconnect(uid, cid)
    await svc.disconnect(uid, cid)  # повтор — no-op
    assert await token_repo.get(uid, cid) is None


@pytest.mark.asyncio
async def test_status_for_all_states() -> None:
    uid = uuid4()
    connected_cid = uuid4()
    expired_cid = uuid4()
    missing_cid = uuid4()
    token_repo = _TokenRepoStub()
    token_repo.seed(
        _token_record(uid, connected_cid, expires_at=_NOW + timedelta(hours=1))
    )
    token_repo.seed(
        _token_record(uid, expired_cid, refresh_failed_at=_NOW)
    )
    svc = _service(conn=_ConnRepoStub(_conn(connected_cid)), token=token_repo)

    statuses = await svc.status_for(
        uid, [connected_cid, expired_cid, missing_cid]
    )

    assert statuses[connected_cid] == "connected"
    assert statuses[expired_cid] == "expired"
    assert statuses[missing_cid] == "not_connected"


@pytest.mark.asyncio
async def test_status_expired_when_no_refresh_and_access_dead() -> None:
    uid, cid = uuid4(), uuid4()
    token_repo = _TokenRepoStub()
    token_repo.seed(
        _token_record(
            uid, cid, expires_at=_NOW - timedelta(minutes=1), refresh_token=None
        )
    )
    svc = _service(conn=_ConnRepoStub(_conn(cid)), token=token_repo)

    statuses = await svc.status_for(uid, [cid])
    assert statuses[cid] == "expired"


# ─────────── фасады клиента для management-слоя ───────────


class _MultiClientRepoStub:
    """get отдаёт запись по connection_id из карты (для батч-фасада)."""

    def __init__(self, records: dict[UUID, McpOAuthClientRecord]) -> None:
        self._records = records

    async def get(self, connection_id: UUID) -> McpOAuthClientRecord | None:
        return self._records.get(connection_id)


@pytest.mark.asyncio
async def test_upsert_preregistered_client_with_secret() -> None:
    cid = uuid4()
    client_repo = _ClientRepoStub()
    svc = _service(conn=_ConnRepoStub(_conn(cid)), client=client_repo)

    record = await svc.upsert_preregistered_client(
        cid, client_id="pre", client_secret="s"
    )

    assert record.token_endpoint_auth_method == "client_secret_post"
    assert client_repo.upserted is not None
    assert client_repo.upserted["source"] == "preregistered"


@pytest.mark.asyncio
async def test_upsert_preregistered_client_public_uses_none_method() -> None:
    cid = uuid4()
    svc = _service(conn=_ConnRepoStub(_conn(cid)), client=_ClientRepoStub())

    record = await svc.upsert_preregistered_client(
        cid, client_id="pub", client_secret=None
    )

    assert record.token_endpoint_auth_method == "none"


@pytest.mark.asyncio
async def test_get_client_returns_record() -> None:
    cid = uuid4()
    svc = _service(
        conn=_ConnRepoStub(_conn(cid)), client=_ClientRepoStub(_client_record(cid))
    )
    assert await svc.get_client(cid) is not None


@pytest.mark.asyncio
async def test_get_clients_maps_present_only() -> None:
    cid1, cid2, missing = uuid4(), uuid4(), uuid4()
    repo = _MultiClientRepoStub(
        {cid1: _client_record(cid1), cid2: _client_record(cid2)}
    )
    svc = _service(conn=_ConnRepoStub(_conn(cid1)), client=cast(Any, repo))

    result = await svc.get_clients([cid1, cid2, missing])

    assert set(result.keys()) == {cid1, cid2}
    assert result[cid1].connection_id == cid1


@pytest.mark.asyncio
async def test_get_clients_empty_list() -> None:
    svc = _service(conn=_ConnRepoStub(_conn(uuid4())))
    assert await svc.get_clients([]) == {}
