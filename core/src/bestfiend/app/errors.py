"""Доменные ошибки композиционного корня приложения."""


class AppError(Exception):
    """Базовая ошибка сборки и жизненного цикла приложения."""


class CoreRuntimeNotInitializedError(AppError):
    """Обращение к runtime до того, как lifespan положил его в app.state."""
