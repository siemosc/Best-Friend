"""Connect-обёртка над fastmcp.Client: опрос (discover) и исполнение (call_tool).

`discover` читает instructions + список тулзов; `call_tool` исполняет тул
(таймаут = server.timeout_s). Сбои классифицируются в типизированные подклассы
`McpClientError` (см. _classify): fastmcp сохраняет `httpx.HTTPStatusError` и
SDK-`McpError`, прочее оборачивает в RuntimeError.
"""

from typing import Any

from fastmcp import Client
from fastmcp.client.client import CallToolResult
from fastmcp.client.transports import StreamableHttpTransport
import httpx

from bestfiend.contracts.mcp import ResolvedMcpServer
from bestfiend.mcp.contracts import ToolInfo
from bestfiend.mcp.errors import (
    McpAuthError,
    McpClientError,
    McpConnectError,
    McpProtocolError,
)


class McpClient:
    """Обёртка fastmcp.Client: transport+auth из ResolvedMcpServer, опрос и исполнение."""

    __slots__ = ("_server",)

    def __init__(self, server: ResolvedMcpServer) -> None:
        self._server = server

    def _build_client(self) -> Client:
        """Собирает fastmcp.Client с HTTP-transport; auth по наличию токена."""
        # auth применяется по наличию токена, не по auth_type: bearer и oauth-access
        # едут одним заголовком, none-сервер приходит с auth_token=None.
        transport = StreamableHttpTransport(
            self._server.url, auth=self._server.auth_token
        )
        return Client(transport)

    async def discover(self) -> tuple[str | None, list[ToolInfo]]:
        """Подключается, читает instructions + tools. Любой сбой → McpClientError."""
        client = self._build_client()
        try:
            async with client:
                init = client.initialize_result
                instructions = init.instructions if init is not None else None
                raw_tools = await client.list_tools()
        except Exception as exc:
            raise _classify(exc) from exc
        return instructions, [_to_tool_info(tool) for tool in raw_tools]

    async def call_tool(
        self, name: str, arguments: dict[str, Any], meta: dict[str, Any] | None = None
    ) -> CallToolResult:
        """Вызывает тул; таймаут = server.timeout_s, meta → нативный `_meta` запроса (None = не шлёт)."""
        client = self._build_client()
        try:
            async with client:
                # raise_on_error=False: сбой самого тула (is_error) — валидный результат
                # для модели, не транспортная ошибка. Транспорт (McpError/httpx) бросается.
                return await client.call_tool(
                    name,
                    arguments,
                    timeout=self._server.timeout_s,
                    raise_on_error=False,
                    meta=meta,
                )
        except Exception as exc:
            raise _classify(exc) from exc


def _classify(exc: Exception) -> McpClientError:
    """Классифицирует исключение fastmcp/httpx в типизированную ошибку клиента."""
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status in (401, 403):
            return McpAuthError(f"auth rejected (HTTP {status})")
        return McpProtocolError(f"server returned HTTP {status}")
    if isinstance(exc, httpx.TransportError):
        return McpConnectError(f"transport error: {exc}")
    return McpProtocolError(f"protocol error: {exc}")


def _to_tool_info(tool: Any) -> ToolInfo:
    """Нормализует fastmcp Tool в ToolInfo (description None → '')."""
    return ToolInfo(
        name=tool.name,
        description=tool.description or "",
        input_schema=tool.inputSchema,
    )
