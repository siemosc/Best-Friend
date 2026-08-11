"""Доменные ошибки storage MCP-подключений."""


class McpStorageError(Exception):
    """Базовая ошибка MCP-storage."""

    error_code = "MCP_STORAGE_ERROR"
    status_code = 500

    def __init__(self, message: str) -> None:
        super().__init__(message)


class McpConnectionNotFoundError(McpStorageError):
    """MCP-подключение по connection_id не найдено."""

    error_code = "MCP_CONNECTION_NOT_FOUND"
    status_code = 404


class McpSubscriptionNotFoundError(McpStorageError):
    """Подписка (user_id, connection_id) не найдена."""

    error_code = "MCP_SUBSCRIPTION_NOT_FOUND"
    status_code = 404


class McpStorageUnavailableError(McpStorageError):
    """Ошибка DB-backend при MCP-storage операции."""

    error_code = "MCP_STORAGE_UNAVAILABLE"
    status_code = 503


class McpConnectionConflictError(McpStorageError):
    """Конфликт уникальности имени подключения (mcp_connections.name занят)."""

    error_code = "MCP_CONNECTION_CONFLICT"
    status_code = 409


class McpSubscriptionConflictError(McpStorageError):
    """FK-нарушение подписки: user_id или connection_id не существует."""

    error_code = "MCP_SUBSCRIPTION_CONFLICT"
    status_code = 409


class McpValidationError(McpStorageError):
    """Нарушение инварианта на API-уровне (public требует auth_type='none')."""

    error_code = "MCP_VALIDATION"
    status_code = 400


class McpSystemConnectionError(McpStorageError):
    """Защищённая системная connection: удаление is_system запрещено."""

    error_code = "MCP_SYSTEM_PROTECTED"
    status_code = 409
