"""McpResolveService: подстановка живого access для oauth, исключение без токена.

Юнит на стабах репозитория подписок и oauth-сервиса. Проверяем контракт резолвера
для графа: non-oauth проходит как есть, oauth получает свежий токен или выпадает.
"""

from typing import Any, cast
from uuid import UUID, uuid4

import pytest

from bestfiend.contracts.mcp import ResolvedMcpServer
from bestfiend.control_plane.mcp.resolve import McpResolveService


def _resolved(**overrides: Any) -> ResolvedMcpServer:
    base: dict[str, Any] = {
        "connection_id": uuid4(),
        "name": "srv",
        "url": "https://example.com/mcp",
        "transport": "http_stream",
        "auth_type": "none",
        "timeout_s": 30.0,
        "is_public": True,
        "auth_token": None,
        "disabled_tools": [],
    }
    base.update(overrides)
    return ResolvedMcpServer(**base)


class _SubRepoStub:
    def __init__(self, servers: list[ResolvedMcpServer]) -> None:
        self._servers = servers

    async def list_for_user(self, user_id: UUID) -> list[ResolvedMcpServer]:
        return self._servers


class _OAuthStub:
    def __init__(self, tokens: dict[UUID, str | None]) -> None:
        self._tokens = tokens

    async def fresh_access_token(
        self, user_id: UUID, connection_id: UUID
    ) -> str | None:
        return self._tokens.get(connection_id)


def _service(
    servers: list[ResolvedMcpServer], tokens: dict[UUID, str | None]
) -> McpResolveService:
    return McpResolveService(
        subscription_repository=cast(Any, _SubRepoStub(servers)),
        oauth_service=cast(Any, _OAuthStub(tokens)),
    )


@pytest.mark.asyncio
async def test_non_oauth_passthrough_unchanged() -> None:
    server = _resolved(auth_type="none")
    result = await _service([server], {}).list_for_user(uuid4())
    assert result == [server]


@pytest.mark.asyncio
async def test_bearer_passthrough_keeps_token() -> None:
    server = _resolved(auth_type="bearer", auth_token="static-tok")
    result = await _service([server], {}).list_for_user(uuid4())
    assert result[0].auth_token == "static-tok"


@pytest.mark.asyncio
async def test_oauth_gets_fresh_access_token() -> None:
    server = _resolved(auth_type="oauth", auth_token=None)
    result = await _service(
        [server], {server.connection_id: "live-access"}
    ).list_for_user(uuid4())
    assert len(result) == 1
    assert result[0].auth_token == "live-access"


@pytest.mark.asyncio
async def test_oauth_without_token_excluded() -> None:
    server = _resolved(auth_type="oauth", auth_token=None)
    result = await _service([server], {server.connection_id: None}).list_for_user(
        uuid4()
    )
    assert result == []


@pytest.mark.asyncio
async def test_mixed_list_keeps_order_and_drops_dead_oauth() -> None:
    plain = _resolved(auth_type="none")
    live_oauth = _resolved(auth_type="oauth")
    dead_oauth = _resolved(auth_type="oauth")
    tokens: dict[UUID, str | None] = {
        live_oauth.connection_id: "access",
        dead_oauth.connection_id: None,
    }
    result = await _service([plain, live_oauth, dead_oauth], tokens).list_for_user(
        uuid4()
    )
    assert [s.connection_id for s in result] == [
        plain.connection_id,
        live_oauth.connection_id,
    ]
    assert result[1].auth_token == "access"
