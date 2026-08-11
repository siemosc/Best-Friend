"""Доменные ошибки модуля ai."""


class AIConfigError(ValueError):
    """Невалидный конфиг модельного вызова (пустой provider/model и т.п.).

    Наследует ValueError на переходный период: старые ловцы `except ValueError`
    продолжают работать, новый код ловит `AIConfigError` точечно.
    """
