"""Доменные ошибки assistant-среза."""


class AssistantConfigError(Exception):
    """Базовая ошибка assistant-среза."""

    error_code = "ASSISTANT_CONFIG_ERROR"
    status_code = 500

    def __init__(self, message: str) -> None:
        super().__init__(message)


class AssistantConfigUnavailableError(AssistantConfigError):
    """Ошибка DB-backend."""

    error_code = "ASSISTANT_CONFIG_UNAVAILABLE"
    status_code = 503


class AssistantConfigNotFoundError(AssistantConfigError):
    """Запись user_assistant_configs не найдена (после reset/update без bootstrap)."""

    error_code = "ASSISTANT_CONFIG_NOT_FOUND"
    status_code = 404
