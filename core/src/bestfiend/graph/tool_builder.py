"""Сборка нативных LangChain `StructuredTool` для графа из MCP-discovery.

`build_mcp_tools` превращает пары (ResolvedMcpServer, ServerDiscovery) в namespaced
`StructuredTool`'ы (closure зовёт `McpClient.call_tool` на raw-имя) + каталог
`ToolServerEntryView` для промпта. Имя тула для модели = `{server.name}__{raw}`; маппинг
зашит в closure (в loop не ищется). `disabled_tools` вычитаются по raw-имени до
namespacing. Тулы artifact-aware (`response_format="content_and_artifact"`).
"""

from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from langchain_core.tools import StructuredTool

from bestfiend.contracts.artifacts import ArtifactRef
from bestfiend.contracts.mcp import ResolvedMcpServer
from bestfiend.graph.state import ToolEntryView, ToolServerEntryView
from bestfiend.mcp.client import McpClient
from bestfiend.mcp.coercion import coerce_tool_result
from bestfiend.mcp.contracts import ServerDiscovery, ToolInfo


ToolResult = tuple[str, list[ArtifactRef] | None]


def build_mcp_tools(
    resolved: Sequence[ResolvedMcpServer],
    discoveries: Sequence[ServerDiscovery],
    request_meta: dict[str, Any] | None = None,
) -> tuple[dict[str, StructuredTool], list[ToolServerEntryView], dict[str, str]]:
    """Собирает namespaced StructuredTool'ы + каталог + serial-маппинг (фейлы пропускаются).

    `serial_tool_servers`: namespaced tool → connection_id, только для серверов с
    `supports_parallel_tool_calls=false` — tools-нода сериализует вызовы к ним.
    `request_meta` запекается в closure каждого тула → уезжает нативным `_meta` в call_tool.
    """
    by_connection = {server.connection_id: server for server in resolved}
    tools_by_name: dict[str, StructuredTool] = {}
    catalog: list[ToolServerEntryView] = []
    serial_tool_servers: dict[str, str] = {}
    for discovery in discoveries:
        if discovery.failure is not None:
            continue
        server = by_connection.get(discovery.connection_id)
        if server is None:
            continue
        disabled = set(server.disabled_tools)
        views: list[ToolEntryView] = []
        for tool in discovery.tools:
            if tool.name in disabled:
                continue
            namespaced = f"{server.name}__{tool.name}"
            tools_by_name[namespaced] = _build_tool(
                server, tool, namespaced, request_meta
            )
            if not server.supports_parallel_tool_calls:
                serial_tool_servers[namespaced] = str(server.connection_id)
            views.append(ToolEntryView(namespaced, tool.description, tool.input_schema))
        catalog.append(
            ToolServerEntryView(server.name, discovery.instructions, tuple(views))
        )
    return tools_by_name, catalog, serial_tool_servers


def _build_tool(
    server: ResolvedMcpServer,
    tool: ToolInfo,
    namespaced: str,
    request_meta: dict[str, Any] | None,
) -> StructuredTool:
    """StructuredTool под один MCP-тул: namespaced-имя, closure на raw-имя, artifact-aware."""
    return StructuredTool.from_function(
        coroutine=_make_caller(server, tool.name, request_meta),
        name=namespaced,
        description=tool.description,
        args_schema=tool.input_schema,
        infer_schema=False,
        response_format="content_and_artifact",
    )


def _make_caller(
    server: ResolvedMcpServer, raw_name: str, request_meta: dict[str, Any] | None
) -> Callable[..., Awaitable[ToolResult]]:
    """Closure: зовёт call_tool на raw-имя сервера (+request_meta как `_meta`) и коэрсит результат."""

    async def _call(**kwargs: Any) -> ToolResult:
        result = await McpClient(server).call_tool(raw_name, kwargs, meta=request_meta)
        return coerce_tool_result(result)

    return _call
