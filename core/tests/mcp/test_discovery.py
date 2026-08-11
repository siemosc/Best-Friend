"""discover_servers: параллельный опрос + graceful degradation (фейл изолирован).

Мок McpClient (patch на импорт в bestfiend.mcp.discovery). Каждый фейл McpClient.discover
должен превращаться в ServerDiscovery.failure нужного kind, не валя остальные серверы.
"""

import asyncio
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from bestfiend.contracts.mcp import ResolvedMcpServer
from bestfiend.mcp.discovery import discover_servers
from bestfiend.mcp.errors import McpAuthError, McpConnectError, McpProtocolError
from bestfiend.mcp.settings import McpDiscoverySettings


def _server(name: str) -> ResolvedMcpServer:
    return ResolvedMcpServer(
        connection_id=uuid4(),
        name=name,
        url="https://example.com/mcp",
        transport="http_stream",
        auth_type="none",
        timeout_s=30.0,
        is_public=True,
        auth_token=None,
        disabled_tools=[],
    )


def _settings(timeout_s: float = 5.0) -> McpDiscoverySettings:
    return McpDiscoverySettings(mcp_discovery_timeout_s=timeout_s)


def _ok_client() -> AsyncMock:
    client = AsyncMock()
    client.discover.return_value = ("instr", [])
    return client


@pytest.mark.asyncio
async def test_discover_empty_list_returns_empty() -> None:
    assert await discover_servers([], _settings()) == []


@pytest.mark.asyncio
async def test_discover_parallel_all_success_preserves_order() -> None:
    servers = [_server("a"), _server("b"), _server("c")]
    with patch("bestfiend.mcp.discovery.McpClient", return_value=_ok_client()):
        results = await discover_servers(servers, _settings())
    assert [r.name for r in results] == ["a", "b", "c"]
    assert all(r.failure is None for r in results)
    assert all(r.instructions == "instr" for r in results)


@pytest.mark.asyncio
async def test_discover_one_failure_isolated_others_ok() -> None:
    def _client_for(server: ResolvedMcpServer) -> AsyncMock:
        client = AsyncMock()
        if server.name == "bad":
            client.discover.side_effect = McpConnectError("refused")
        else:
            client.discover.return_value = ("instr", [])
        return client

    servers = [_server("ok"), _server("bad"), _server("ok2")]
    with patch("bestfiend.mcp.discovery.McpClient", side_effect=_client_for):
        results = await discover_servers(servers, _settings())

    by_name = {r.name: r for r in results}
    assert by_name["ok"].failure is None
    assert by_name["ok2"].failure is None
    assert by_name["bad"].failure is not None
    assert by_name["bad"].failure.kind == "unreachable"


@pytest.mark.asyncio
async def test_discover_timeout_yields_timeout_failure() -> None:
    async def _slow(*_args: object, **_kwargs: object) -> tuple[None, list[object]]:
        await asyncio.sleep(10)
        return None, []

    slow_client = AsyncMock()
    slow_client.discover.side_effect = _slow

    with patch("bestfiend.mcp.discovery.McpClient", return_value=slow_client):
        results = await discover_servers([_server("slow")], _settings(timeout_s=0.05))

    assert len(results) == 1
    assert results[0].failure is not None
    assert results[0].failure.kind == "timeout"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("exc", "expected_kind"),
    [
        (McpAuthError("auth"), "auth"),
        (McpConnectError("conn"), "unreachable"),
        (McpProtocolError("proto"), "protocol"),
    ],
)
async def test_discover_error_kinds_mapped(exc: Exception, expected_kind: str) -> None:
    client = AsyncMock()
    client.discover.side_effect = exc
    with patch("bestfiend.mcp.discovery.McpClient", return_value=client):
        results = await discover_servers([_server("x")], _settings())
    assert results[0].failure is not None
    assert results[0].failure.kind == expected_kind
