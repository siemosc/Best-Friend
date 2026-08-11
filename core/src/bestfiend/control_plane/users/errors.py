"""Доменные ошибки управления пользователями."""


class UserError(Exception):
    """Базовая ошибка users-модуля."""

    error_code = "USER_ERROR"
    status_code = 500

    def __init__(self, message: str) -> None:
        super().__init__(message)


class UserNotFoundError(UserError):
    """Пользователь не найден."""

    error_code = "USER_NOT_FOUND"
    status_code = 404


class UserUnavailableError(UserError):
    """Ошибка DB-backend users."""

    error_code = "USER_UNAVAILABLE"
    status_code = 503


class UserConflictError(UserError):
    """Конфликт уникальности идентификаторов пользователя."""

    error_code = "USER_CONFLICT"
    status_code = 409


class SelfEditNotAllowedError(UserError):
    """Администратор пытается изменить собственные роль или статус."""

    error_code = "USER_SELF_EDIT_NOT_ALLOWED"
    status_code = 400
