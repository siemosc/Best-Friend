"""build_mcp_tools: namespacing, вычет disabled_tools, каталог, closure на raw-имя.

Мок McpClient (patch на импорт в tool_builder) — проверяем, что closure namespaced-тула
зовёт call_tool с RAW-именем нужного сервера.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest

from bestfiend.contracts.mcp import ResolvedMcpServer
from bestfiend.graph.tool_builder import build_mcp_tools
from bestfiend.mcp.contracts import DiscoveryFailure, ServerDiscovery, ToolInfo


def _server(
    name: str,
    conn_id: UUID,
    *,
    disabled: list[str] | None = None,
    supports_parallel: bool = True,
) -> ResolvedMcpServer:
    return ResolvedMcpServer(
        connection_id=conn_id,
        name=name,
        url="https://example.com/mcp",
        transport="http_stream",
        auth_type="none",
        timeout_s=30.0,
        is_public=True,
        auth_token=None,
        disabled_tools=disabled or [],
        supports_parallel_tool_calls=supports_parallel,
    )


def _tool(name: str) -> ToolInfo:
    return ToolInfo(
        name=name, description=f"{name} desc", input_schema={"type": "object"}
    )


def _discovery(conn_id: UUID, name: str, tools: list[ToolInfo]) -> ServerDiscovery:
    return ServerDiscovery(
        connection_id=conn_id,
        name=name,
        instructions="instr",
        tools=tools,
        failure=None,
    )


def test_namespacing_and_catalog() -> None:
    cid = uuid4()
    server = _server("websearch", cid)
    discovery = _discovery(cid, "websearch", [_tool("search"), _tool("fetch")])
    tools, catalog, _serial = build_mcp_tools([server], [discovery])

    assert set(tools) == {"websearch__search", "websearch__fetch"}
    entry = catalog[0]
    assert entry.name == "websearch"
    assert entry.instructions == "instr"
    assert {view.name for view in entry.tools} == {
        "websearch__search",
        "websearch__fetch",
    }


def test_disabled_tools_subtracted_by_raw_name() -> None:
    cid = uuid4()
    server = _server("websearch", cid, disabled=["fetch"])
    discovery = _discovery(cid, "websearch", [_tool("search"), _tool("fetch")])
    tools, catalog, _serial = build_mcp_tools([server], [discovery])

    assert set(tools) == {"websearch__search"}
    assert {view.name for view in catalog[0].tools} == {"websearch__search"}


def test_failed_server_skipped() -> None:
    cid = uuid4()
    server = _server("broken", cid)
    discovery = ServerDiscovery(
        connection_id=cid,
        name="broken",
        instructions=None,
        tools=[],
        failure=DiscoveryFailure(kind="unreachable", message="refused"),
    )
    tools, catalog, _serial = build_mcp_tools([server], [discovery])

    assert tools == {}
    assert catalog == []


@pytest.mark.asyncio
async def test_closure_calls_call_tool_with_raw_name() -> None:
    cid = uuid4()
    server = _server("websearch", cid)
    tools, _, _ = build_mcp_tools(
        [server], [_discovery(cid, "websearch", [_tool("search")])]
    )

    mock_client = AsyncMock()
    mock_client.call_tool.return_value = SimpleNamespace(
        is_error=False, structured_content=None, content=[], data=None
    )
    with patch("bestfiend.graph.tool_builder.McpClient", return_value=mock_client):
        result = await tools["websearch__search"].ainvoke(
            {
                "name": "websearch__search",
                "args": {"q": "x"},
                "id": "c1",
                "type": "tool_call",
            }
        )

    # closure зовёт call_tool с RAW-именем "search" (не namespaced); meta=None без request_meta
    mock_client.call_tool.assert_awaited_once_with("search", {"q": "x"}, meta=None)
    # response_format="content_and_artifact" + tool_call с id → ToolMessage; generic content → ""
    assert result.content == ""


@pytest.mark.asyncio
async def test_closure_forwards_request_meta() -> None:
    """request_meta запечён в closure → уходит как meta в call_tool."""
    cid = uuid4()
    server = _server("websearch", cid)
    tools, _, _ = build_mcp_tools(
        [server],
        [_discovery(cid, "websearch", [_tool("search")])],
        {"user_id": "u1"},
    )

    mock_client = AsyncMock()
    mock_client.call_tool.return_value = SimpleNamespace(
        is_error=False, structured_content=None, content=[], data=None
    )
    with patch("bestfiend.graph.tool_builder.McpClient", return_value=mock_client):
        await tools["websearch__search"].ainvoke(
            {
                "name": "websearch__search",
                "args": {"q": "x"},
                "id": "c1",
                "type": "tool_call",
            }
        )

    mock_client.call_tool.assert_awaited_once_with(
        "search", {"q": "x"}, meta={"user_id": "u1"}
    )


def test_serial_map_only_for_non_parallel_servers() -> None:
    par_id, ser_id = uuid4(), uuid4()
    parallel = _server("par", par_id)  # supports_parallel=True (дефолт)
    serial = _server("ser", ser_id, supports_parallel=False)
    tools, _catalog, serial_map = build_mcp_tools(
        [parallel, serial],
        [
            _discovery(par_id, "par", [_tool("a")]),
            _discovery(ser_id, "ser", [_tool("b")]),
        ],
    )
    # параллельный сервер не в serial-map; непараллельный — его тулзы → connection_id
    assert "par__a" not in serial_map
    assert serial_map == {"ser__b": str(ser_id)}
    assert set(tools) == {"par__a", "ser__b"}
