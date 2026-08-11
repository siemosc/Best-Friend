"""PostgreSQL-репозитории capability control_plane.

Содержит: UserRepository (reader + identity-creation + admin writers),
SessionRepository, BindingCodeRepository, AuthUserRepository.
"""

from typing import Any
from uuid import UUID

import asyncpg

from bestfiend.control_plane.db import ControlPlaneDatabaseClient
from bestfiend.control_plane.users.errors import (
    UserConflictError,
    UserUnavailableError,
)
from bestfiend.control_plane.users.models import (
    UserProfile,
    UserRole,
    UserStatus,
)


_USER_COLUMNS = (
    "user_id, role, status, telegram_chat_id, discord_user_id, "
    "login, timezone, city, country, "
    "created_at, updated_at"
)

_PROFILE_UPDATABLE_FIELDS: frozenset[str] = frozenset(
    {"timezone", "city", "country"},
)


class UserRepository:
    """Read-доступ к таблице users. Оборачивает asyncpg-ошибки в доменные."""

    __slots__ = ("_db",)

    def __init__(self, db_client: ControlPlaneDatabaseClient) -> None:
        self._db = db_client

    async def get_by_id(self, user_id: UUID) -> UserProfile | None:
        """Возвращает профиль по user_id или None."""
        query = f"SELECT {_USER_COLUMNS} FROM users WHERE user_id = $1"  # nosec B608 — SQL из внутренних констант, значения через $N-параметры
        try:
            row = await self._db.fetch_one(query, user_id)
        except asyncpg.PostgresError as exc:
            raise UserUnavailableError(
                f"Failed to fetch user by user_id={user_id}"
            ) from exc
        return _row_to_profile(row) if row else None

    async def list_all(self) -> list[UserProfile]:
        """Возвращает всех пользователей, отсортированных по created_at."""
        query = f"SELECT {_USER_COLUMNS} FROM users ORDER BY created_at ASC"  # nosec B608 — SQL из внутренних констант, значения через $N-параметры
        try:
            rows = await self._db.fetch(query)
        except asyncpg.PostgresError as exc:
            raise UserUnavailableError("Failed to list users") from exc
        return [_row_to_profile(row) for row in rows]

    # ── Identity-creation lookups + writers ────────────────────────────

    async def get_by_telegram_chat_id(
        self,
        telegram_chat_id: int,
    ) -> UserProfile | None:
        """Возвращает профиль по telegram_chat_id или None."""
        query = f"SELECT {_USER_COLUMNS} FROM users WHERE telegram_chat_id = $1"  # nosec B608 — SQL из внутренних констант, значения через $N-параметры
        try:
            row = await self._db.fetch_one(query, telegram_chat_id)
        except asyncpg.PostgresError as exc:
            raise UserUnavailableError(
                f"Failed to fetch user by telegram_chat_id={telegram_chat_id}"
            ) from exc
        return _row_to_profile(row) if row else None

    async def create_pending(self, *, telegram_chat_id: int) -> UserProfile:
        """Создаёт pending-пользователя с привязкой к Telegram chat_id."""
        query = f"""
            INSERT INTO users (telegram_chat_id)
            VALUES ($1)
            RETURNING {_USER_COLUMNS}
        """  # nosec B608 — SQL из внутренних констант, значения через $N-параметры
        try:
            row = await self._db.fetch_one(query, telegram_chat_id)
        except asyncpg.UniqueViolationError as exc:
            raise UserConflictError(
                f"User with telegram_chat_id={telegram_chat_id} already exists"
            ) from exc
        except asyncpg.PostgresError as exc:
            raise UserUnavailableError("Failed to create pending user") from exc

        if row is None:
            raise UserUnavailableError("Create pending user returned no row")
        return _row_to_profile(row)

    async def link_discord(
        self,
        user_id: UUID,
        *,
        discord_user_id: str,
    ) -> UserProfile:
        """Привязывает Discord ID к пользователю."""
        query = f"""
            UPDATE users
            SET discord_user_id = $2, updated_at = NOW()
            WHERE user_id = $1
            RETURNING {_USER_COLUMNS}
        """  # nosec B608 — SQL из внутренних констант, значения через $N-параметры
        try:
            row = await self._db.fetch_one(query, user_id, discord_user_id)
        except asyncpg.UniqueViolationError as exc:
            raise UserConflictError(
                f"discord_user_id={discord_user_id} already bound to another user"
            ) from exc
        except asyncpg.PostgresError as exc:
            raise UserUnavailableError(
                f"Failed to link discord_user_id for user_id={user_id}"
            ) from exc
        if row is None:
            raise UserUnavailableError(
                f"link_discord returned no row for user_id={user_id}"
            )
        return _row_to_profile(row)

    # ── Admin writers ──────────────────────────────────────────────────

    async def update_role(self, user_id: UUID, *, role: UserRole) -> UserProfile:
        """Меняет role. UserNotFoundError мапается выше через service-layer."""
        query = f"""
            UPDATE users
            SET role = $2, updated_at = NOW()
            WHERE user_id = $1
            RETURNING {_USER_COLUMNS}
        """  # nosec B608 — SQL из внутренних констант, значения через $N-параметры
        return await self._update_returning(query, user_id, role)

    async def update_status(
        self,
        user_id: UUID,
        *,
        status: UserStatus,
    ) -> UserProfile:
        """Меняет status."""
        query = f"""
            UPDATE users
            SET status = $2, updated_at = NOW()
            WHERE user_id = $1
            RETURNING {_USER_COLUMNS}
        """  # nosec B608 — SQL из внутренних констант, значения через $N-параметры
        return await self._update_returning(query, user_id, status)

    async def update_profile_fields(
        self,
        user_id: UUID,
        fields: dict[str, str | None],
    ) -> UserProfile:
        """Частичное обновление полей профиля.

        `fields` содержит только реально переданные поля. `None` означает
        явный `SET column = NULL` (очистка). Пустой dict — no-op, возвращается
        текущий профиль без UPDATE.
        """
        updates: list[str] = []
        values: list[Any] = []
        for field, value in fields.items():
            if field not in _PROFILE_UPDATABLE_FIELDS:
                raise ValueError(
                    f"Unknown field '{field}' for users.update_profile_fields"
                )
            placeholder = f"${len(values) + 2}"
            updates.append(f"{field} = {placeholder}")
            values.append(value)

        if not updates:
            existing = await self.get_by_id(user_id)
            if existing is None:
                raise UserUnavailableError(f"user_id={user_id} not found")
            return existing

        updates.append("updated_at = NOW()")
        query = (
            f"UPDATE users SET {', '.join(updates)} "  # nosec B608 — SQL из внутренних констант, значения через $N-параметры
            f"WHERE user_id = $1 RETURNING {_USER_COLUMNS}"
        )
        try:
            row = await self._db.fetch_one(query, user_id, *values)
        except asyncpg.PostgresError as exc:
            raise UserUnavailableError(f"Failed to update user_id={user_id}") from exc
        if row is None:
            raise UserUnavailableError(f"Update returned no row for user_id={user_id}")
        return _row_to_profile(row)

    async def _update_returning(
        self,
        query: str,
        user_id: UUID,
        value: Any,
    ) -> UserProfile:
        try:
            row = await self._db.fetch_one(query, user_id, value)
        except asyncpg.PostgresError as exc:
            raise UserUnavailableError(f"Failed to update user_id={user_id}") from exc
        if row is None:
            raise UserUnavailableError(f"Update returned no row for user_id={user_id}")
        return _row_to_profile(row)


def _row_to_profile(row: Any) -> UserProfile:
    """Преобразует asyncpg.Record в UserProfile."""
    return UserProfile(
        user_id=row["user_id"],
        role=row["role"],
        status=row["status"],
        telegram_chat_id=row["telegram_chat_id"],
        discord_user_id=row["discord_user_id"],
        login=row["login"],
        timezone=row["timezone"],
        city=row["city"],
        country=row["country"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )
