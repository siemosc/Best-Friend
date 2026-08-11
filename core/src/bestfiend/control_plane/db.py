"""Порт БД для репозиториев control_plane.

Реализацию (пул asyncpg + миграции) владеет и собирает app/db.py —
композиционный корень передаёт клиент сюда через конструкторы репозиториев.
"""

from typing import Any, Protocol


class ControlPlaneDatabaseClient(Protocol):
    """Минимальный контракт DB клиента.

    Методы пробрасывают asyncpg исключения как есть.
    """

    async def execute(self, query: str, *args: object) -> str:
        """Выполняет SQL запрос изменения."""
        ...

    async def fetch(self, query: str, *args: object) -> list[Any]:
        """Выполняет SELECT и возвращает строки."""
        ...

    async def fetch_one(self, query: str, *args: object) -> Any | None:
        """Выполняет SELECT и возвращает одну строку."""
        ...
