"""Доменные ошибки model_registry."""


class ModelRegistryError(Exception):
    """Базовая ошибка model_registry."""

    error_code = "MODEL_REGISTRY_ERROR"
    status_code = 500

    def __init__(self, message: str) -> None:
        super().__init__(message)


class ModelNotFoundError(ModelRegistryError):
    """Запрошенный model ID не найден."""

    error_code = "MODEL_NOT_FOUND"
    status_code = 404
