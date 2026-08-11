"""PostgreSQL-репозитории аутентификации и сессий."""

from datetime import datetime
from typing import Any
from uuid import UUID

import asyncpg

from bestfiend.control_plane.auth.errors import (
    AuthUnavailableError,
    LoginConflictError,
)
from bestfiend.control_plane.auth.models import (
    AuthCredentials,
    BindingCodeRecord,
    SessionRecord,
)
from bestfiend.control_plane.db import ControlPlaneDatabaseClient


_SESSION_COLUMNS = "session_id, user_id, created_at, expires_at"
_BINDING_COLUMNS = "code, user_id, expires_at, created_at"


class SessionRepository:
    """Доступ к таблице sessions."""

    __slots__ = ("_db",)

    def __init__(self, db_client: ControlPlaneDatabaseClient) -> None:
        self._db = db_client

    async def get_by_id(self, session_id: UUID) -> SessionRecord | None:
        """Возвращает сессию по session_id или None."""
        query = f"SELECT {_SESSION_COLUMNS} FROM sessions WHERE session_id = $1"  # nosec B608
        try:
            row = await self._db.fetch_one(query, session_id)
        except asyncpg.PostgresError as exc:
            raise AuthUnavailableError(
                f"Failed to fetch session session_id={session_id}"
            ) from exc
        return _row_to_session(row) if row else None

    async def delete_by_id(self, session_id: UUID) -> bool:
        """Удаляет сессию. Возвращает True, если запись удалена."""
        query = "DELETE FROM sessions WHERE session_id = $1"
        try:
            status = await self._db.execute(query, session_id)
        except asyncpg.PostgresError as exc:
            raise AuthUnavailableError(
                f"Failed to delete session session_id={session_id}"
            ) from exc
        return "DELETE 1" in status

    async def create(
        self,
        *,
        user_id: UUID,
        expires_at: datetime,
    ) -> SessionRecord:
        """Создаёт новую сессию с заданным expires_at."""
        query = f"""
            INSERT INTO sessions (user_id, expires_at)
            VALUES ($1, $2)
            RETURNING {_SESSION_COLUMNS}
        """  # nosec B608
        try:
            row = await self._db.fetch_one(query, user_id, expires_at)
        except asyncpg.ForeignKeyViolationError as exc:
            raise AuthUnavailableError(
                f"user_id={user_id} does not exist in users"
            ) from exc
        except asyncpg.PostgresError as exc:
            raise AuthUnavailableError(
                f"Failed to create session for user_id={user_id}"
            ) from exc
        if row is None:
            raise AuthUnavailableError(
                f"create_session returned no row for user_id={user_id}"
            )
        return _row_to_session(row)


class BindingCodeRepository:
    """CRUD для таблицы auth_binding_codes."""

    __slots__ = ("_db",)

    def __init__(self, db_client: ControlPlaneDatabaseClient) -> None:
        self._db = db_client

    async def create_or_replace(
        self,
        *,
        code: str,
        user_id: UUID,
        expires_at: datetime,
    ) -> BindingCodeRecord:
        """Создаёт код для пользователя, заменяя его предыдущий код."""
        delete_query = "DELETE FROM auth_binding_codes WHERE user_id = $1"
        insert_query = f"""
            INSERT INTO auth_binding_codes (code, user_id, expires_at)
            VALUES ($1, $2, $3)
            RETURNING {_BINDING_COLUMNS}
        """  # nosec B608
        try:
            await self._db.execute(delete_query, user_id)
            row = await self._db.fetch_one(insert_query, code, user_id, expires_at)
        except asyncpg.UniqueViolationError as exc:
            raise AuthUnavailableError(
                f"binding_code collision on code={code}"
            ) from exc
        except asyncpg.ForeignKeyViolationError as exc:
            raise AuthUnavailableError(
                f"user_id={user_id} does not exist in users"
            ) from exc
        except asyncpg.PostgresError as exc:
            raise AuthUnavailableError(
                f"Failed to create binding code for user_id={user_id}"
            ) from exc
        if row is None:
            raise AuthUnavailableError(
                f"create_or_replace returned no row for user_id={user_id}"
            )
        return _row_to_binding(row)

    async def get_by_code(self, code: str) -> BindingCodeRecord | None:
        """Возвращает запись по коду или None."""
        query = f"SELECT {_BINDING_COLUMNS} FROM auth_binding_codes WHERE code = $1"  # nosec B608
        try:
            row = await self._db.fetch_one(query, code)
        except asyncpg.PostgresError as exc:
            raise AuthUnavailableError(f"Failed to fetch binding code={code}") from exc
        return _row_to_binding(row) if row else None

    async def delete_by_code(self, code: str) -> bool:
        """Удаляет код. Возвращает True, если запись удалена."""
        query = "DELETE FROM auth_binding_codes WHERE code = $1"
        try:
            status = await self._db.execute(query, code)
        except asyncpg.PostgresError as exc:
            raise AuthUnavailableError(f"Failed to delete binding code={code}") from exc
        return "DELETE 1" in status


class AuthUserRepository:
    """Доступ к логину и хешу пароля пользователя."""

    __slots__ = ("_db",)

    def __init__(self, db_client: ControlPlaneDatabaseClient) -> None:
        self._db = db_client

    async def get_credentials_by_login(self, login: str) -> AuthCredentials | None:
        """Возвращает учётные данные по логину."""
        query = (
            "SELECT user_id, login, password_hash, status "
            "FROM users WHERE login = $1 AND password_hash IS NOT NULL"
        )
        try:
            row = await self._db.fetch_one(query, login)
        except asyncpg.PostgresError as exc:
            raise AuthUnavailableError(
                f"Failed to fetch credentials by login={login}"
            ) from exc
        if row is None:
            return None
        return AuthCredentials(
            user_id=row["user_id"],
            login=row["login"],
            password_hash=row["password_hash"],
            status=row["status"],
        )

    async def set_credentials(
        self,
        *,
        user_id: UUID,
        login: str,
        password_hash: str,
    ) -> None:
        """Устанавливает логин и хеш пароля пользователя."""
        query = """
            UPDATE users
            SET login = $2, password_hash = $3, updated_at = NOW()
            WHERE user_id = $1
        """
        try:
            status = await self._db.execute(query, user_id, login, password_hash)
        except asyncpg.UniqueViolationError as exc:
            raise LoginConflictError(f"login={login} already taken") from exc
        except asyncpg.PostgresError as exc:
            raise AuthUnavailableError(
                f"Failed to set credentials for user_id={user_id}"
            ) from exc
        if "UPDATE 1" not in status:
            raise AuthUnavailableError(
                f"set_credentials affected no row for user_id={user_id}"
            )

    async def get_password_hash(self, user_id: UUID) -> str | None:
        """Возвращает хеш пароля пользователя или None."""
        query = "SELECT password_hash FROM users WHERE user_id = $1"
        try:
            row = await self._db.fetch_one(query, user_id)
        except asyncpg.PostgresError as exc:
            raise AuthUnavailableError(
                f"Failed to fetch password_hash for user_id={user_id}"
            ) from exc
        if row is None:
            return None
        return row["password_hash"]

    async def update_password_hash(
        self,
        *,
        user_id: UUID,
        password_hash: str,
    ) -> None:
        """Обновляет хеш пароля, не меняя логин."""
        query = """
            UPDATE users
            SET password_hash = $2, updated_at = NOW()
            WHERE user_id = $1
        """
        try:
            status = await self._db.execute(query, user_id, password_hash)
        except asyncpg.PostgresError as exc:
            raise AuthUnavailableError(
                f"Failed to update password_hash for user_id={user_id}"
            ) from exc
        if "UPDATE 1" not in status:
            raise AuthUnavailableError(
                f"update_password_hash affected no row for user_id={user_id}"
            )


def _row_to_binding(row: Any) -> BindingCodeRecord:
    return BindingCodeRecord(
        code=row["code"],
        user_id=row["user_id"],
        expires_at=row["expires_at"],
        created_at=row["created_at"],
    )


def _row_to_session(row: Any) -> SessionRecord:
    return SessionRecord(
        session_id=row["session_id"],
        user_id=row["user_id"],
        created_at=row["created_at"],
        expires_at=row["expires_at"],
    )
