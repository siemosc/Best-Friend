"""Доменные ошибки ядра памяти.

База — MemoryDomainError (не MemoryError: последнее — builtin про нехватку
памяти процесса). Веб-фасад памяти несёт свою иерархию (memory/web_facade/errors.py).
"""


class MemoryDomainError(Exception):
    """Базовая ошибка ядра памяти."""


class MemoryDatabaseUnavailableError(MemoryDomainError):
    """Пул БД памяти не инициализирован или соединение не поднялось."""


class MemoryPersistError(MemoryDomainError):
    """Персист не выполнил ожидаемую операцию (INSERT не вернул строку и т.п.)."""
