"""PostgreSQL клиент memory (использует схему core; schema-owner — app/db.py)."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Protocol

import asyncpg
from loguru import logger
from pgvector.asyncpg import register_vector
from pydantic_settings import BaseSettings, SettingsConfigDict

from bestfiend.memory.errors import MemoryDatabaseUnavailableError


class MemoryDatabaseSettings(BaseSettings):
    """Настройки PostgreSQL для memory."""

    postgres_host: str = "localhost"
    postgres_port: int = 5433
    postgres_db: str = "bestfiend"
    postgres_user: str = "bestfiend"
    postgres_password: str = "changeme"
    postgres_pool_min_size: int = 5
    postgres_pool_max_size: int = 20

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


class DatabaseExecutor(Protocol):
    """Исполнитель запросов: pool-клиент или executor открытой транзакции."""

    async def execute(self, query: str, *args: object) -> str:
        """Выполняет SQL запрос изменения."""
        ...

    async def fetch(self, query: str, *args: object) -> list[Any]:
        """Выполняет SELECT и возвращает строки."""
        ...

    async def fetch_one(self, query: str, *args: object) -> Any | None:
        """Выполняет SELECT и возвращает одну строку."""
        ...


class MemoryDatabaseClient(DatabaseExecutor, Protocol):
    """Контракт DB клиента для memory: запросы + транзакционный scope."""

    def transaction(self) -> Any:
        """Async context manager: DatabaseExecutor на одном соединении в транзакции."""
        ...


class TransactionExecutor:
    """Лёгкий executor поверх одного соединения внутри открытой транзакции."""

    __slots__ = ("_conn",)

    def __init__(self, conn: asyncpg.pool.PoolConnectionProxy) -> None:
        self._conn = conn

    async def execute(self, query: str, *args: object) -> str:
        """Выполняет SQL запрос изменения в рамках транзакции."""
        return await self._conn.execute(query, *args)

    async def fetch(self, query: str, *args: object) -> list[asyncpg.Record]:
        """Выполняет SELECT в рамках транзакции."""
        return await self._conn.fetch(query, *args)

    async def fetch_one(self, query: str, *args: object) -> asyncpg.Record | None:
        """Выполняет SELECT одной строки в рамках транзакции."""
        return await self._conn.fetchrow(query, *args)


class MemoryPostgreSQLClient:
    """PostgreSQL клиент memory. Схему не мигрирует — schema-owner = app/db.py."""

    __slots__ = ("_pool", "_settings")

    def __init__(self, settings: MemoryDatabaseSettings | None = None) -> None:
        self._settings = settings or MemoryDatabaseSettings()
        self._pool: asyncpg.Pool | None = None

    async def connect(self) -> None:
        """Подключает pool. Схему не мигрирует."""
        try:
            logger.info(
                "MemoryPostgreSQLClient: connecting to {}:{}",
                self._settings.postgres_host,
                self._settings.postgres_port,
            )
            self._pool = await asyncpg.create_pool(
                host=self._settings.postgres_host,
                port=self._settings.postgres_port,
                database=self._settings.postgres_db,
                user=self._settings.postgres_user,
                password=self._settings.postgres_password,
                min_size=self._settings.postgres_pool_min_size,
                max_size=self._settings.postgres_pool_max_size,
                command_timeout=60,
                # pgvector-кодек на каждое соединение пула: параметры/чтение
                # vector(1024) работают как list/ndarray без ручных литералов.
                init=_init_connection,
                # Таблицы memory живут в схеме core; public — fallback для
                # extension-типов. Перебивает дефолт роли bestfiend: тот всё ещё
                # указывает на схему contextforge (CF снесён, дроп схемы отложен).
                server_settings={"search_path": "core,public"},
            )
        except (asyncpg.PostgresError, OSError) as exc:
            raise MemoryDatabaseUnavailableError(
                f"Не удалось подключиться к PostgreSQL: {exc}"
            ) from exc

    async def disconnect(self) -> None:
        """Закрывает pool."""
        if self._pool is not None:
            await self._pool.close()

    async def execute(self, query: str, *args: object) -> str:
        """Выполняет SQL запрос изменения."""
        pool = self._require_pool()
        async with pool.acquire() as conn:
            return await conn.execute(query, *args)

    async def fetch(self, query: str, *args: object) -> list[asyncpg.Record]:
        """Выполняет SELECT и возвращает все строки."""
        pool = self._require_pool()
        async with pool.acquire() as conn:
            return await conn.fetch(query, *args)

    async def fetch_one(self, query: str, *args: object) -> asyncpg.Record | None:
        """Выполняет SELECT и возвращает одну строку."""
        pool = self._require_pool()
        async with pool.acquire() as conn:
            return await conn.fetchrow(query, *args)

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[TransactionExecutor]:
        """Открывает транзакцию на одном соединении (атомарные батч-вставки заметок)."""
        pool = self._require_pool()
        async with pool.acquire() as conn, conn.transaction():
            yield TransactionExecutor(conn)

    def _require_pool(self) -> asyncpg.Pool:
        """Возвращает pool или бросает RuntimeError если не инициализирован."""
        if self._pool is None:
            raise MemoryDatabaseUnavailableError("Connection pool не инициализирован")
        return self._pool


async def _init_connection(conn: asyncpg.Connection) -> None:
    """Init-callback пула: регистрирует pgvector-кодек (тип vector в public)."""
    await register_vector(conn)
