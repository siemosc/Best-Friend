"""OAuth storage repositories: маппинг строк, одноразовость take, CAS-запись.

Юнит на in-memory стабах (без реальной БД). SQL-семантику (DELETE ... RETURNING,
UPDATE ... WHERE refresh_token = expected) исполняет PostgreSQL — здесь
воспроизводим её в стабе, чтобы проверить маппинг строк в записи и трансляцию
command tag в bool/int; реальный SQL проверяется смоуком миграции.
"""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import pytest

from bestfiend.control_plane.mcp.errors import McpStorageUnavailableError
from bestfiend.control_plane.mcp.oauth.repository import (
    McpOAuthClientRepository,
    McpOAuthFlowRepository,
    McpOAuthTokenRepository,
    _command_rowcount,
)


_NOW = datetime.now(UTC)


def _client_row(
    connection_id: UUID,
    *,
    client_id: str = "cid",
    client_secret: str | None = "secret",
    token_endpoint_auth_method: str = "client_secret_post",
    source: str = "preregistered",
    client_secret_expires_at: datetime | None = None,
) -> dict[str, Any]:
    return {
        "connection_id": connection_id,
        "client_id": client_id,
        "client_secret": client_secret,
        "token_endpoint_auth_method": token_endpoint_auth_method,
        "source": source,
        "client_secret_expires_at": client_secret_expires_at,
        "created_at": _NOW,
        "updated_at": None,
    }


def _flow_row(
    state: str,
    user_id: UUID,
    connection_id: UUID,
    *,
    scope: str | None = "openid email",
) -> dict[str, Any]:
    return {
        "state": state,
        "user_id": user_id,
        "connection_id": connection_id,
        "code_verifier": "verifier",
        "redirect_uri": "https://app/api/mcp/oauth/callback",
        "token_endpoint": "https://as/token",
        "issuer": "https://as",
        "resource": "https://mcp/",
        "scope": scope,
        "expires_at": _NOW + timedelta(minutes=10),
        "created_at": _NOW,
    }


def _token_row(
    user_id: UUID,
    connection_id: UUID,
    *,
    access_token: str = "access",
    refresh_token: str | None = "refresh",
    expires_at: datetime | None = None,
    scope: str | None = "openid",
    refresh_failed_at: datetime | None = None,
) -> dict[str, Any]:
    return {
        "user_id": user_id,
        "connection_id": connection_id,
        "access_token": access_token,
        "refresh_token": refresh_token,
        "expires_at": expires_at,
        "scope": scope,
        "token_endpoint": "https://as/token",
        "refresh_failed_at": refresh_failed_at,
        "created_at": _NOW,
        "updated_at": None,
    }


class _FetchOneStub:
    """fetch_one → заданная строка (или None), либо бросает заданное исключение."""

    def __init__(self, row: Any = None, *, raises: Exception | None = None) -> None:
        self._row = row
        self._raises = raises

    async def fetch_one(self, query: str, *args: object) -> Any:
        if self._raises is not None:
            raise self._raises
        return self._row


class _FetchManyStub:
    """fetch → заданный список строк, либо бросает."""

    def __init__(self, rows: list[Any], *, raises: Exception | None = None) -> None:
        self._rows = rows
        self._raises = raises

    async def fetch(self, query: str, *args: object) -> list[Any]:
        if self._raises is not None:
            raise self._raises
        return self._rows


class _ExecuteStub:
    """execute → заданный command tag ('UPDATE 1'), либо бросает."""

    def __init__(self, status: str = "UPDATE 1", *, raises: Exception | None = None) -> None:
        self._status = status
        self._raises = raises

    async def execute(self, query: str, *args: object) -> str:
        if self._raises is not None:
            raise self._raises
        return self._status


class _TakeStub:
    """DELETE ... RETURNING одноразово: первый вызов отдаёт строку, дальше None."""

    def __init__(self, row: Any) -> None:
        self._row = row

    async def fetch_one(self, query: str, *args: object) -> Any:
        row, self._row = self._row, None
        return row


# ─────────── McpOAuthClientRepository ───────────


@pytest.mark.asyncio
async def test_client_get_maps_row() -> None:
    conn_id = uuid4()
    db = _FetchOneStub(_client_row(conn_id, source="dcr", client_secret=None,
                                   token_endpoint_auth_method="none"))
    repo = McpOAuthClientRepository(db)  # type: ignore[arg-type]
    rec = await repo.get(conn_id)
    assert rec is not None
    assert rec.connection_id == conn_id
    assert rec.source == "dcr"
    assert rec.client_secret is None
    assert rec.token_endpoint_auth_method == "none"


@pytest.mark.asyncio
async def test_client_get_missing_returns_none() -> None:
    db = _FetchOneStub(None)
    repo = McpOAuthClientRepository(db)  # type: ignore[arg-type]
    assert await repo.get(uuid4()) is None


@pytest.mark.asyncio
async def test_client_upsert_maps_row() -> None:
    conn_id = uuid4()
    db = _FetchOneStub(_client_row(conn_id))
    repo = McpOAuthClientRepository(db)  # type: ignore[arg-type]
    rec = await repo.upsert(
        conn_id,
        client_id="cid",
        client_secret="secret",
        token_endpoint_auth_method="client_secret_post",
        source="preregistered",
    )
    assert rec.client_id == "cid"
    assert rec.token_endpoint_auth_method == "client_secret_post"


@pytest.mark.asyncio
async def test_client_get_postgres_error_maps_to_unavailable() -> None:
    db = _FetchOneStub(raises=asyncpg.PostgresError("down"))
    repo = McpOAuthClientRepository(db)  # type: ignore[arg-type]
    with pytest.raises(McpStorageUnavailableError):
        await repo.get(uuid4())


# ─────────── McpOAuthFlowRepository ───────────


@pytest.mark.asyncio
async def test_flow_create_maps_row() -> None:
    user_id, conn_id = uuid4(), uuid4()
    db = _FetchOneStub(_flow_row("st", user_id, conn_id))
    repo = McpOAuthFlowRepository(db)  # type: ignore[arg-type]
    rec = await repo.create(
        state="st",
        user_id=user_id,
        connection_id=conn_id,
        code_verifier="verifier",
        redirect_uri="https://app/api/mcp/oauth/callback",
        token_endpoint="https://as/token",
        issuer="https://as",
        resource="https://mcp/",
        scope="openid email",
        expires_at=_NOW + timedelta(minutes=10),
    )
    assert rec.state == "st"
    assert rec.issuer == "https://as"
    assert rec.resource == "https://mcp/"


@pytest.mark.asyncio
async def test_flow_take_is_one_shot() -> None:
    user_id, conn_id = uuid4(), uuid4()
    db = _TakeStub(_flow_row("st", user_id, conn_id))
    repo = McpOAuthFlowRepository(db)  # type: ignore[arg-type]
    first = await repo.take("st")
    assert first is not None
    assert first.state == "st"
    second = await repo.take("st")
    assert second is None


@pytest.mark.asyncio
async def test_flow_take_missing_returns_none() -> None:
    db = _FetchOneStub(None)
    repo = McpOAuthFlowRepository(db)  # type: ignore[arg-type]
    assert await repo.take("nope") is None


@pytest.mark.asyncio
async def test_flow_purge_expired_returns_count() -> None:
    db = _ExecuteStub("DELETE 3")
    repo = McpOAuthFlowRepository(db)  # type: ignore[arg-type]
    assert await repo.purge_expired() == 3


@pytest.mark.asyncio
async def test_flow_purge_expired_nothing_deleted() -> None:
    db = _ExecuteStub("DELETE 0")
    repo = McpOAuthFlowRepository(db)  # type: ignore[arg-type]
    assert await repo.purge_expired() == 0


# ─────────── McpOAuthTokenRepository ───────────


@pytest.mark.asyncio
async def test_token_get_maps_row() -> None:
    user_id, conn_id = uuid4(), uuid4()
    db = _FetchOneStub(_token_row(user_id, conn_id, access_token="A", refresh_token="R"))
    repo = McpOAuthTokenRepository(db)  # type: ignore[arg-type]
    rec = await repo.get(user_id, conn_id)
    assert rec is not None
    assert rec.access_token == "A"
    assert rec.refresh_token == "R"
    assert rec.refresh_failed_at is None


@pytest.mark.asyncio
async def test_token_get_missing_returns_none() -> None:
    db = _FetchOneStub(None)
    repo = McpOAuthTokenRepository(db)  # type: ignore[arg-type]
    assert await repo.get(uuid4(), uuid4()) is None


@pytest.mark.asyncio
async def test_token_upsert_maps_row() -> None:
    user_id, conn_id = uuid4(), uuid4()
    db = _FetchOneStub(_token_row(user_id, conn_id))
    repo = McpOAuthTokenRepository(db)  # type: ignore[arg-type]
    rec = await repo.upsert(
        user_id,
        conn_id,
        access_token="access",
        refresh_token="refresh",
        expires_at=None,
        scope="openid",
        token_endpoint="https://as/token",
    )
    assert rec.access_token == "access"
    assert rec.token_endpoint == "https://as/token"


@pytest.mark.asyncio
async def test_token_list_for_user_maps_rows() -> None:
    user_id = uuid4()
    rows = [_token_row(user_id, uuid4()), _token_row(user_id, uuid4())]
    db = _FetchManyStub(rows)
    repo = McpOAuthTokenRepository(db)  # type: ignore[arg-type]
    recs = await repo.list_for_user(user_id)
    assert len(recs) == 2
    assert all(r.user_id == user_id for r in recs)


@pytest.mark.asyncio
async def test_token_update_cas_matched_returns_true() -> None:
    db = _ExecuteStub("UPDATE 1")
    repo = McpOAuthTokenRepository(db)  # type: ignore[arg-type]
    ok = await repo.update_tokens_if_refresh_matches(
        uuid4(),
        uuid4(),
        access_token="new",
        refresh_token="new_refresh",
        expires_at=None,
        scope=None,
        expected_refresh_token="old_refresh",
    )
    assert ok is True


@pytest.mark.asyncio
async def test_token_update_cas_mismatch_returns_false() -> None:
    db = _ExecuteStub("UPDATE 0")
    repo = McpOAuthTokenRepository(db)  # type: ignore[arg-type]
    ok = await repo.update_tokens_if_refresh_matches(
        uuid4(),
        uuid4(),
        access_token="new",
        refresh_token="new_refresh",
        expires_at=None,
        scope=None,
        expected_refresh_token="stale",
    )
    assert ok is False


@pytest.mark.asyncio
async def test_token_mark_refresh_failed_matched_returns_true() -> None:
    db = _ExecuteStub("UPDATE 1")
    repo = McpOAuthTokenRepository(db)  # type: ignore[arg-type]
    ok = await repo.mark_refresh_failed(
        uuid4(), uuid4(), expected_refresh_token="old_refresh"
    )
    assert ok is True


@pytest.mark.asyncio
async def test_token_mark_refresh_failed_mismatch_returns_false() -> None:
    db = _ExecuteStub("UPDATE 0")
    repo = McpOAuthTokenRepository(db)  # type: ignore[arg-type]
    ok = await repo.mark_refresh_failed(
        uuid4(), uuid4(), expected_refresh_token="stale"
    )
    assert ok is False


@pytest.mark.asyncio
async def test_token_upsert_postgres_error_maps_to_unavailable() -> None:
    db = _FetchOneStub(raises=asyncpg.PostgresError("down"))
    repo = McpOAuthTokenRepository(db)  # type: ignore[arg-type]
    with pytest.raises(McpStorageUnavailableError):
        await repo.upsert(
            uuid4(),
            uuid4(),
            access_token="a",
            refresh_token=None,
            expires_at=None,
            scope=None,
            token_endpoint="https://as/token",
        )


# ─────────── _command_rowcount ───────────


def test_command_rowcount_parses_tags() -> None:
    assert _command_rowcount("UPDATE 1") == 1
    assert _command_rowcount("DELETE 3") == 0 + 3
    assert _command_rowcount("UPDATE 0") == 0
    assert _command_rowcount("") == 0
    assert _command_rowcount("WEIRD") == 0
