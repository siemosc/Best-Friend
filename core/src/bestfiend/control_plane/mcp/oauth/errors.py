"""Доменные ошибки OAuth-тракта MCP.

error_code — стабильный код для маппинга в HTTP и проброса во фронт query-параметром
(app/routes переводит McpOAuthError в `?oauth_error={error_code}`), тот же паттерн
класс-атрибутов, что и в control_plane/mcp/errors.py.
"""


class McpOAuthError(Exception):
    """Базовая ошибка OAuth-подсистемы MCP."""

    error_code = "mcp_oauth_error"
    status_code = 500

    def __init__(self, message: str) -> None:
        super().__init__(message)


class McpOAuthClientMissingError(McpOAuthError):
    """Нет OAuth-кред для connection и DCR недоступен."""

    error_code = "mcp_oauth_client_missing"
    status_code = 409


class McpOAuthDiscoveryError(McpOAuthError):
    """Сервер не отдал метадату authorization server."""

    error_code = "mcp_oauth_discovery_failed"
    status_code = 502


class McpOAuthFlowNotFoundError(McpOAuthError):
    """state неизвестен, протух или принадлежит другому юзеру."""

    error_code = "mcp_oauth_flow_expired"
    status_code = 410


class McpOAuthExchangeError(McpOAuthError):
    """Token endpoint отверг обмен authorization code."""

    error_code = "mcp_oauth_exchange_failed"
    status_code = 502


class McpOAuthRegistrationError(McpOAuthError):
    """DCR не удался или вернул неподдержанный token_endpoint_auth_method."""

    error_code = "mcp_oauth_registration_failed"
    status_code = 502


class McpOAuthRefreshRejectedError(McpOAuthError):
    """Refresh отвергнут authorization server (invalid_grant).

    Внутренняя: наружу в HTTP не мапится — сервис ловит её и переводит запись
    в статус expired через mark_refresh_failed.
    """

    error_code = "mcp_oauth_refresh_rejected"
