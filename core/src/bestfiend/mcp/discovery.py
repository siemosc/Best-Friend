"""Stateless параллельный опрос MCP-серверов (зовёт GraphRuntime перед стартом графа).

Без кэша: каждый вызов опрашивает серверы вживую. Любой фейл одного сервера →
`ServerDiscovery.failure` (graceful degradation), остальные не страдают.
"""

import asyncio

from bestfiend.contracts.mcp import ResolvedMcpServer
from bestfiend.mcp.client import McpClient
from bestfiend.mcp.contracts import (
    DiscoveryFailure,
    DiscoveryFailureKind,
    ServerDiscovery,
)
from bestfiend.mcp.errors import McpAuthError, McpClientError, McpConnectError
from bestfiend.mcp.settings import McpDiscoverySettings


async def discover_servers(
    servers: list[ResolvedMcpServer],
    settings: McpDiscoverySettings | None = None,
) -> list[ServerDiscovery]:
    """Опрашивает серверы параллельно; каждый фейл изолирован в ServerDiscovery.failure."""
    settings = settings or McpDiscoverySettings()
    timeout_s = settings.mcp_discovery_timeout_s
    return list(
        await asyncio.gather(*(_discover_one(server, timeout_s) for server in servers))
    )


async def _discover_one(server: ResolvedMcpServer, timeout_s: float) -> ServerDiscovery:
    """Опрашивает один сервер; любой фейл (timeout|auth|unreachable|protocol) → failure."""
    try:
        async with asyncio.timeout(timeout_s):
            instructions, tools = await McpClient(server).discover()
    except TimeoutError:
        return _failed(server, "timeout", f"discovery timed out after {timeout_s}s")
    except McpAuthError as exc:
        return _failed(server, "auth", str(exc))
    except McpConnectError as exc:
        return _failed(server, "unreachable", str(exc))
    except McpClientError as exc:
        return _failed(server, "protocol", str(exc))
    return ServerDiscovery(
        connection_id=server.connection_id,
        name=server.name,
        instructions=instructions,
        tools=tools,
        failure=None,
    )


def _failed(
    server: ResolvedMcpServer, kind: DiscoveryFailureKind, message: str
) -> ServerDiscovery:
    """Собирает ServerDiscovery с фейлом (без tools/instructions)."""
    return ServerDiscovery(
        connection_id=server.connection_id,
        name=server.name,
        instructions=None,
        tools=[],
        failure=DiscoveryFailure(kind=kind, message=message),
    )
