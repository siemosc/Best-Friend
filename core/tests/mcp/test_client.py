"""McpClient.discover: connect+initialize+list_tools + классификация сбоев.

Мок fastmcp.Client (patch на импорт в bestfiend.mcp.client). SimpleNamespace для
мок-Tool — MagicMock(name=...) задаёт имя мока, а не атрибут .name.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
import pytest

from bestfiend.contracts.mcp import ResolvedMcpServer
from bestfiend.mcp.client import McpClient
from bestfiend.mcp.errors import McpAuthError, McpConnectError, McpProtocolError


def _server(
    name: str = "srv", *, auth_type: str = "none", auth_token: str | None = None
) -> ResolvedMcpServer:
    return ResolvedMcpServer(
        connection_id=uuid4(),
        name=name,
        url="https://example.com/mcp",
        transport="http_stream",
        auth_type=auth_type,  # type: ignore[arg-type]
        timeout_s=30.0,
        is_public=True,
        auth_token=auth_token,
        disabled_tools=[],
    )


def _mock_client(
    *,
    instructions: str | None = None,
    tools: list[object] | None = None,
    list_tools_exc: Exception | None = None,
) -> AsyncMock:
    client = AsyncMock()
    client.__aexit__.return_value = False  # не глотать исключения из body
    client.initialize_result = (
        SimpleNamespace(instructions=instructions) if instructions is not None else None
    )
    if list_tools_exc is not None:
        client.list_tools.side_effect = list_tools_exc
    else:
        client.list_tools.return_value = tools or []
    return client


def _http_status_exc(status: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://example.com/mcp")
    response = httpx.Response(status, request=request)
    return httpx.HTTPStatusError(f"HTTP {status}", request=request, response=response)


@pytest.mark.asyncio
async def test_discover_success_returns_instructions_and_tools() -> None:
    tool = SimpleNamespace(
        name="search", description="Web search", inputSchema={"type": "object"}
    )
    mock = _mock_client(instructions="I search the web", tools=[tool])
    with patch("bestfiend.mcp.client.Client", return_value=mock):
        instructions, tools = await McpClient(_server()).discover()
    assert instructions == "I search the web"
    assert len(tools) == 1
    assert tools[0].name == "search"
    assert tools[0].description == "Web search"
    assert tools[0].input_schema == {"type": "object"}


@pytest.mark.asyncio
async def test_discover_none_initialize_result_yields_none_instructions() -> None:
    mock = _mock_client(instructions=None, tools=[])
    with patch("bestfiend.mcp.client.Client", return_value=mock):
        instructions, tools = await McpClient(_server()).discover()
    assert instructions is None
    assert tools == []


@pytest.mark.asyncio
async def test_discover_tool_description_none_normalized_to_empty() -> None:
    tool = SimpleNamespace(name="t", description=None, inputSchema={})
    mock = _mock_client(instructions="x", tools=[tool])
    with patch("bestfiend.mcp.client.Client", return_value=mock):
        _, tools = await McpClient(_server()).discover()
    assert tools[0].description == ""


@pytest.mark.asyncio
async def test_discover_connect_error_maps_to_connect() -> None:
    mock = _mock_client(instructions="x", list_tools_exc=httpx.ConnectError("refused"))
    with (
        patch("bestfiend.mcp.client.Client", return_value=mock),
        pytest.raises(McpConnectError),
    ):
        await McpClient(_server()).discover()


@pytest.mark.asyncio
async def test_discover_http_401_maps_to_auth() -> None:
    mock = _mock_client(instructions="x", list_tools_exc=_http_status_exc(401))
    with (
        patch("bestfiend.mcp.client.Client", return_value=mock),
        pytest.raises(McpAuthError),
    ):
        await McpClient(_server()).discover()


@pytest.mark.asyncio
async def test_discover_http_500_maps_to_protocol() -> None:
    mock = _mock_client(instructions="x", list_tools_exc=_http_status_exc(500))
    with (
        patch("bestfiend.mcp.client.Client", return_value=mock),
        pytest.raises(McpProtocolError),
    ):
        await McpClient(_server()).discover()


@pytest.mark.asyncio
async def test_discover_generic_error_maps_to_protocol() -> None:
    mock = _mock_client(instructions="x", list_tools_exc=RuntimeError("weird"))
    with (
        patch("bestfiend.mcp.client.Client", return_value=mock),
        pytest.raises(McpProtocolError),
    ):
        await McpClient(_server()).discover()


@pytest.mark.asyncio
async def test_call_tool_passes_exec_timeout_and_returns_result() -> None:
    sentinel = object()
    client = AsyncMock()
    client.__aexit__.return_value = False
    client.call_tool.return_value = sentinel
    server = _server()
    with patch("bestfiend.mcp.client.Client", return_value=client):
        result = await McpClient(server).call_tool("search", {"q": "x"})
    assert result is sentinel
    # Таймаут исполнения = server.timeout_s (не discovery); ошибка тула не бросается.
    client.call_tool.assert_awaited_once_with(
        "search", {"q": "x"}, timeout=server.timeout_s, raise_on_error=False, meta=None
    )


@pytest.mark.asyncio
async def test_call_tool_forwards_meta() -> None:
    """meta пробрасывается в нативный `_meta` запроса fastmcp."""
    client = AsyncMock()
    client.__aexit__.return_value = False
    client.call_tool.return_value = object()
    server = _server()
    with patch("bestfiend.mcp.client.Client", return_value=client):
        await McpClient(server).call_tool("search", {"q": "x"}, meta={"user_id": "u1"})
    client.call_tool.assert_awaited_once_with(
        "search",
        {"q": "x"},
        timeout=server.timeout_s,
        raise_on_error=False,
        meta={"user_id": "u1"},
    )


@pytest.mark.asyncio
async def test_call_tool_connect_error_maps_to_connect() -> None:
    client = AsyncMock()
    client.__aexit__.return_value = False
    client.call_tool.side_effect = httpx.ConnectError("refused")
    with (
        patch("bestfiend.mcp.client.Client", return_value=client),
        pytest.raises(McpConnectError),
    ):
        await McpClient(_server()).call_tool("search", {})


# ─────────── auth по наличию токена, не по auth_type ───────────


@pytest.mark.parametrize(
    ("auth_type", "auth_token", "expected"),
    [
        ("none", None, None),
        ("bearer", "bearer-tok", "bearer-tok"),
        ("oauth", "oauth-access", "oauth-access"),  # oauth-access едет тем же заголовком
        ("bearer", None, None),  # тип bearer, но токена нет → без auth
    ],
)
def test_build_client_passes_auth_by_token_presence(
    auth_type: str, auth_token: str | None, expected: str | None
) -> None:
    server = _server(auth_type=auth_type, auth_token=auth_token)
    with (
        patch("bestfiend.mcp.client.Client"),
        patch("bestfiend.mcp.client.StreamableHttpTransport") as transport,
    ):
        McpClient(server)._build_client()
    transport.assert_called_once_with(server.url, auth=expected)
