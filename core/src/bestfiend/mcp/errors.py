"""Внутренние ошибки MCP-обёртки.

Бросаются `McpClient` — и при опросе (discover), и при вызове тулзов
(call_tool). Discovery классифицирует их в `DiscoveryFailure` и наружу не
пробрасывает (graceful degradation).
"""


class McpClientError(Exception):
    """База ошибок обращения к MCP-серверу."""


class McpConnectError(McpClientError):
    """Сервер недоступен: network / DNS / connection refused."""


class McpAuthError(McpClientError):
    """Auth отклонён сервером (401/403) — токен невалиден или протух."""


class McpProtocolError(McpClientError):
    """Некорректный MCP-ответ или прочий сбой протокола."""
