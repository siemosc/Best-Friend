"""PostgreSQL-клиент core: schema-owner общей схемы `core`.

Применяет миграции из `core/scripts/migrations/` через advisory-lock.
Снапшот `001_initial_schema.sql` — точка отсчёта; existing prod-БД
seed'ится через `_apply_migrations` без перенаката DDL по существующим
таблицам (см. legacy auto-seed).
"""

from pathlib import Path

import asyncpg
from loguru import logger

from bestfiend.app.settings import CoreDatabaseSettings


# Уникальный относительно других сервисов: control_plane = 748302,
# artifacts = 748301; core занимает 748303.
_MIGRATION_ADVISORY_LOCK_ID = 748303

# Снапшот схемы. Имя ловится legacy-seed-веткой `_apply_migrations`.
_SNAPSHOT_MIGRATION_NAME = "001_initial_schema.sql"


class CorePostgreSQLClient:
    """PostgreSQL клиент core — schema-owner с авто-миграциями."""

    __slots__ = ("_pool", "_settings")

    def __init__(self, settings: CoreDatabaseSettings | None = None) -> None:
        self._settings = settings or CoreDatabaseSettings()
        self._pool: asyncpg.Pool | None = None

    async def connect(self) -> None:
        """Подключает pool и применяет миграции при `postgres_auto_migrate`."""
        try:
            logger.info(
                "CorePostgreSQLClient: connecting to {}:{}",
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
                # Наши таблицы и _migrations живут в схеме core; public —
                # fallback для extension-типов. Перебивает дефолт роли бд-юзера:
                # тот всё ещё указывает на схему contextforge (CF снесён, дроп схемы отложен).
                server_settings={"search_path": "core,public"},
            )
            if self._settings.postgres_auto_migrate:
                await self._apply_migrations()
        except (asyncpg.PostgresError, OSError) as exc:
            raise RuntimeError(f"Не удалось подключиться к PostgreSQL: {exc}") from exc

    async def disconnect(self) -> None:
        """Закрывает pool."""
        if self._pool is not None:
            await self._pool.close()

    async def execute(self, query: str, *args: object) -> str:
        """Выполняет SQL запрос изменения.

        Пробрасывает asyncpg исключения как есть — вызывающий код
        решает, как их интерпретировать.
        """
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

    def _require_pool(self) -> asyncpg.Pool:
        """Возвращает pool или бросает RuntimeError если не инициализирован."""
        if self._pool is None:
            raise RuntimeError("Connection pool не инициализирован")
        return self._pool

    async def _apply_migrations(self) -> None:
        """Применяет SQL миграции из `core/scripts/migrations/`.

        Использует PostgreSQL advisory lock для защиты от race condition
        при параллельном старте нескольких инстансов. На existing prod-БД
        снапшот сидируется в `_migrations` без применения (legacy seed).
        """
        pool = self._require_pool()
        # db.py = core/src/bestfiend/app/db.py → parents[3] = core/.
        migrations_dir = Path(__file__).resolve().parents[3] / "scripts" / "migrations"
        if not migrations_dir.exists():
            logger.warning("Каталог миграций не найден: {}", migrations_dir)
            return

        async with pool.acquire() as conn:
            await conn.execute(
                "SELECT pg_advisory_lock($1)", _MIGRATION_ADVISORY_LOCK_ID
            )
            try:
                await self._ensure_migrations_table(conn)
                applied = await self._get_applied_migrations(conn)

                # Existing prod-БД имеет таблицы из старых сервисов, но не
                # знает про снапшот → сидируем без apply, иначе snapshot DDL
                # упадёт по существующим таблицам. Маркер — наличие core.users.
                if _SNAPSHOT_MIGRATION_NAME not in applied:
                    legacy = await conn.fetchval("SELECT to_regclass('core.users')")
                    if legacy is not None:
                        logger.info(
                            "Legacy DB detected; auto-seeding _migrations with {}.",
                            _SNAPSHOT_MIGRATION_NAME,
                        )
                        await conn.execute(
                            "INSERT INTO _migrations (name) VALUES ($1) "
                            "ON CONFLICT DO NOTHING",
                            _SNAPSHOT_MIGRATION_NAME,
                        )
                        applied = {*applied, _SNAPSHOT_MIGRATION_NAME}

                migration_files = sorted(migrations_dir.glob("*.sql"))
                pending = [f for f in migration_files if f.name not in applied]
                if not pending:
                    return

                for migration_file in pending:
                    logger.info(
                        "CorePostgreSQLClient: applying migration {}",
                        migration_file.name,
                    )
                    sql = migration_file.read_text(encoding="utf-8")
                    await conn.execute(sql)
                    await conn.execute(
                        "INSERT INTO _migrations (name) VALUES ($1)",
                        migration_file.name,
                    )
            finally:
                await conn.execute(
                    "SELECT pg_advisory_unlock($1)", _MIGRATION_ADVISORY_LOCK_ID
                )

    @staticmethod
    async def _ensure_migrations_table(conn: asyncpg.pool.PoolConnectionProxy) -> None:
        """Создаёт таблицу `_migrations` если её ещё нет."""
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS _migrations (
                id SERIAL PRIMARY KEY,
                name VARCHAR(255) NOT NULL UNIQUE,
                applied_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
            )
        """)

    @staticmethod
    async def _get_applied_migrations(
        conn: asyncpg.pool.PoolConnectionProxy,
    ) -> set[str]:
        """Возвращает множество имён уже применённых миграций."""
        try:
            rows = await conn.fetch("SELECT name FROM _migrations")
            return {row["name"] for row in rows}
        except asyncpg.PostgresError:
            return set()
