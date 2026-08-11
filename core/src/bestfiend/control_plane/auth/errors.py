"""Доменные ошибки среза control_plane (auth + users)."""


class AuthError(Exception):
    """Базовая ошибка auth-модуля."""

    error_code = "AUTH_ERROR"
    status_code = 500

    def __init__(self, message: str) -> None:
        super().__init__(message)


class InvalidSessionError(AuthError):
    """Сессия отсутствует, не найдена или просрочена."""

    error_code = "AUTH_INVALID_SESSION"
    status_code = 401


class InvalidCredentialsError(AuthError):
    """Неверный логин или пароль."""

    error_code = "AUTH_INVALID_CREDENTIALS"
    status_code = 401


class InvalidCurrentPasswordError(AuthError):
    """При смене пароля указан неверный текущий пароль."""

    error_code = "AUTH_INVALID_CURRENT_PASSWORD"
    status_code = 401


class BindingCodeNotFoundError(AuthError):
    """Код привязки не найден."""

    error_code = "AUTH_BINDING_CODE_NOT_FOUND"
    status_code = 404


class BindingCodeExpiredError(AuthError):
    """Код привязки найден, но просрочен."""

    error_code = "AUTH_BINDING_CODE_EXPIRED"
    status_code = 410


class UserStatusError(AuthError):
    """Статус пользователя не позволяет операцию (pending или banned)."""

    error_code = "AUTH_USER_STATUS_FORBIDDEN"
    status_code = 403


class LoginConflictError(AuthError):
    """Login уже занят другим пользователем."""

    error_code = "AUTH_LOGIN_CONFLICT"
    status_code = 409


class ForbiddenError(AuthError):
    """Сессия валидна, но у юзера нет прав на операцию (role-check)."""

    error_code = "AUTH_FORBIDDEN"
    status_code = 403


class AuthUnavailableError(AuthError):
    """Ошибка DB-backend при auth-операции."""

    error_code = "AUTH_UNAVAILABLE"
    status_code = 503
