"""Доменные ошибки artifacts service."""


class ArtifactError(Exception):
    """Базовая ошибка artifacts service."""

    error_code = "ARTIFACT_ERROR"
    status_code = 500

    def __init__(self, message: str) -> None:
        super().__init__(message)


class ArtifactInvalidRequestError(ArtifactError):
    """Ошибка валидации входных данных."""

    error_code = "ARTIFACT_INVALID_REQUEST"
    status_code = 400


class ArtifactNotFoundError(ArtifactError):
    """Артефакт не найден."""

    error_code = "ARTIFACT_NOT_FOUND"
    status_code = 404


class ArtifactTooLargeError(ArtifactError):
    """Payload превышает допустимый размер."""

    error_code = "ARTIFACT_TOO_LARGE"
    status_code = 413


class ArtifactUnsupportedTypeError(ArtifactError):
    """Тип артефакта не поддерживается."""

    error_code = "ARTIFACT_UNSUPPORTED_TYPE"
    status_code = 415


class ArtifactStorageUnavailableError(ArtifactError):
    """Ошибка storage backend."""

    error_code = "ARTIFACT_STORAGE_UNAVAILABLE"
    status_code = 503
