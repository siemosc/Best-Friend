"""Auth repositories: BindingCodeRepository, AuthUserRepository, SessionRepository.create."""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import pytest

from bestfiend.control_plane.auth.errors import (
    AuthUnavailableError,
    LoginConflictError,
)
from bestfiend.control_plane.auth.repository import (
    AuthUserRepository,
    BindingCodeRepository,
    SessionRepository,
)


_NOW = datetime.now(UTC)


class _AsyncpgUniqueViolationError(asyncpg.UniqueViolationError):
    """Прокидываем pretend-UniqueViolation для simulate of UNIQUE constraint."""


class _DBStub:
    """In-memory DB для auth-tables: binding_codes / sessions / users.login."""

    def __init__(self) -> None:
        self._sessions: dict[UUID, dict[str, Any]] = {}
        self._binding_codes: dict[str, dict[str, Any]] = {}
        self._binding_by_user: dict[UUID, str] = {}
        self._users: dict[UUID, dict[str, Any]] = {}
        self._login_index: dict[str, UUID] = {}
        # Sentinel для force-injected UniqueViolation
        self._raise_unique_on_set_credentials = False

    def seed_user(
        self,
        user_id: UUID,
        *,
        login: str | None = None,
        password_hash: str | None = None,
        status: str = "active",
    ) -> None:
        self._users[user_id] = {
            "user_id": user_id,
            "login": login,
            "password_hash": password_hash,
            "status": status,
        }
        if login is not None:
            self._login_index[login] = user_id

    async def execute(self, query: str, *args: object) -> str:
        if "DELETE FROM auth_binding_codes WHERE user_id" in query:
            user_id = args[0]
            assert isinstance(user_id, UUID)
            old_code = self._binding_by_user.pop(user_id, None)
            if old_code is not None:
                self._binding_codes.pop(old_code, None)
                return "DELETE 1"
            return "DELETE 0"
        if "DELETE FROM auth_binding_codes WHERE code" in query:
            code = args[0]
            assert isinstance(code, str)
            row = self._binding_codes.pop(code, None)
            if row is not None:
                self._binding_by_user.pop(row["user_id"], None)
                return "DELETE 1"
            return "DELETE 0"
        if "DELETE FROM sessions WHERE session_id" in query:
            sid = args[0]
            assert isinstance(sid, UUID)
            if self._sessions.pop(sid, None) is not None:
                return "DELETE 1"
            return "DELETE 0"
        if "UPDATE users" in query and "login" in query and "password_hash" in query:
            if self._raise_unique_on_set_credentials:
                self._raise_unique_on_set_credentials = False
                raise _AsyncpgUniqueViolationError("login already taken")
            user_id, login, password_hash = args
            assert isinstance(user_id, UUID)
            assert isinstance(login, str)
            assert isinstance(password_hash, str)
            user = self._users.get(user_id)
            if user is None:
                return "UPDATE 0"
            user["login"] = login
            user["password_hash"] = password_hash
            self._login_index[login] = user_id
            return "UPDATE 1"
        if "UPDATE users" in query and "password_hash" in query:
            user_id, password_hash = args
            assert isinstance(user_id, UUID)
            assert isinstance(password_hash, str)
            user = self._users.get(user_id)
            if user is None:
                return "UPDATE 0"
            user["password_hash"] = password_hash
            return "UPDATE 1"
        return "OK"

    async def fetch(self, query: str, *args: object) -> list[Any]:
        return []

    async def fetch_one(self, query: str, *args: object) -> Any:
        if "INSERT INTO auth_binding_codes" in query:
            code, user_id, expires_at = args
            assert isinstance(code, str)
            assert isinstance(user_id, UUID)
            row = {
                "code": code,
                "user_id": user_id,
                "expires_at": expires_at,
                "created_at": _NOW,
            }
            self._binding_codes[code] = row
            self._binding_by_user[user_id] = code
            return row
        if "SELECT" in query and "auth_binding_codes" in query:
            code = args[0]
            assert isinstance(code, str)
            return self._binding_codes.get(code)
        if "INSERT INTO sessions" in query:
            user_id, expires_at = args
            assert isinstance(user_id, UUID)
            session_id = uuid4()
            row = {
                "session_id": session_id,
                "user_id": user_id,
                "created_at": _NOW,
                "expires_at": expires_at,
            }
            self._sessions[session_id] = row
            return row
        if "SELECT" in query and "FROM sessions" in query:
            sid = args[0]
            assert isinstance(sid, UUID)
            return self._sessions.get(sid)
        if (
            "SELECT user_id, login, password_hash, status" in query
            and "FROM users WHERE login" in query
        ):
            login = args[0]
            assert isinstance(login, str)
            uid = self._login_index.get(login)
            if uid is None:
                return None
            user = self._users[uid]
            if user["password_hash"] is None:
                return None
            return user
        if "SELECT password_hash FROM users" in query:
            user_id = args[0]
            assert isinstance(user_id, UUID)
            user = self._users.get(user_id)
            if user is None:
                return None
            return {"password_hash": user["password_hash"]}
        return None


@pytest.mark.asyncio
async def test_binding_code_create_or_replace_returns_record() -> None:
    db = _DBStub()
    repo = BindingCodeRepository(db)  # type: ignore[arg-type]
    user_id = uuid4()
    expires_at = _NOW + timedelta(minutes=10)

    record = await repo.create_or_replace(
        code="123456",
        user_id=user_id,
        expires_at=expires_at,
    )

    assert record.code == "123456"
    assert record.user_id == user_id
    assert record.expires_at == expires_at


@pytest.mark.asyncio
async def test_binding_code_replace_supersedes_old() -> None:
    """Повторный create — старый код удалён, новый возвращён."""
    db = _DBStub()
    repo = BindingCodeRepository(db)  # type: ignore[arg-type]
    user_id = uuid4()
    expires = _NOW + timedelta(minutes=10)

    await repo.create_or_replace(code="111111", user_id=user_id, expires_at=expires)
    record = await repo.create_or_replace(
        code="222222",
        user_id=user_id,
        expires_at=expires,
    )

    assert record.code == "222222"
    assert await repo.get_by_code("111111") is None
    assert (await repo.get_by_code("222222")) is not None


@pytest.mark.asyncio
async def test_binding_code_get_unknown_returns_none() -> None:
    db = _DBStub()
    repo = BindingCodeRepository(db)  # type: ignore[arg-type]
    assert await repo.get_by_code("000000") is None


@pytest.mark.asyncio
async def test_binding_code_delete_returns_bool() -> None:
    db = _DBStub()
    repo = BindingCodeRepository(db)  # type: ignore[arg-type]
    await repo.create_or_replace(
        code="333333",
        user_id=uuid4(),
        expires_at=_NOW + timedelta(minutes=10),
    )
    assert await repo.delete_by_code("333333") is True
    assert await repo.delete_by_code("nonexistent") is False


@pytest.mark.asyncio
async def test_session_create_returns_record() -> None:
    db = _DBStub()
    repo = SessionRepository(db)  # type: ignore[arg-type]
    user_id = uuid4()
    expires = _NOW + timedelta(days=30)

    record = await repo.create(user_id=user_id, expires_at=expires)

    assert record.user_id == user_id
    assert record.expires_at == expires


@pytest.mark.asyncio
async def test_auth_user_get_credentials_by_login_returns_none_if_missing() -> None:
    db = _DBStub()
    repo = AuthUserRepository(db)  # type: ignore[arg-type]
    assert await repo.get_credentials_by_login("ghost") is None


@pytest.mark.asyncio
async def test_auth_user_set_and_get_credentials_roundtrip() -> None:
    db = _DBStub()
    repo = AuthUserRepository(db)  # type: ignore[arg-type]
    user_id = uuid4()
    db.seed_user(user_id, status="active")

    await repo.set_credentials(
        user_id=user_id,
        login="alice",
        password_hash="hash-1",
    )

    creds = await repo.get_credentials_by_login("alice")
    assert creds is not None
    assert creds.user_id == user_id
    assert creds.login == "alice"
    assert creds.password_hash == "hash-1"


@pytest.mark.asyncio
async def test_auth_user_set_credentials_login_conflict() -> None:
    """UniqueViolation на login → LoginConflictError."""
    db = _DBStub()
    repo = AuthUserRepository(db)  # type: ignore[arg-type]
    user_id = uuid4()
    db.seed_user(user_id, status="active")
    db._raise_unique_on_set_credentials = True

    with pytest.raises(LoginConflictError):
        await repo.set_credentials(
            user_id=user_id,
            login="taken",
            password_hash="hash",
        )


@pytest.mark.asyncio
async def test_auth_user_update_password_hash_persists() -> None:
    db = _DBStub()
    repo = AuthUserRepository(db)  # type: ignore[arg-type]
    user_id = uuid4()
    db.seed_user(user_id, password_hash="old-hash")

    await repo.update_password_hash(user_id=user_id, password_hash="new-hash")

    assert await repo.get_password_hash(user_id) == "new-hash"


@pytest.mark.asyncio
async def test_auth_user_update_password_hash_missing_user_raises() -> None:
    """UPDATE 0 → AuthUnavailableError."""
    db = _DBStub()
    repo = AuthUserRepository(db)  # type: ignore[arg-type]

    with pytest.raises(AuthUnavailableError):
        await repo.update_password_hash(user_id=uuid4(), password_hash="x")
