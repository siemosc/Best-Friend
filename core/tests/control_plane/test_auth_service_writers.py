"""AuthService writers: login / bind_credentials / change_password / generate_binding_code."""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest

from bestfiend.control_plane.auth.errors import (
    AuthUnavailableError,
    BindingCodeExpiredError,
    BindingCodeNotFoundError,
    InvalidCredentialsError,
    InvalidCurrentPasswordError,
    UserStatusError,
)
from bestfiend.control_plane.auth.models import (
    AuthCredentials,
    BindingCodeRecord,
    SessionRecord,
)
from bestfiend.control_plane.auth.passwords import hash_password
from bestfiend.control_plane.auth.service import AuthService
from bestfiend.control_plane.settings import AuthSettings
from bestfiend.control_plane.users.models import UserProfile


_NOW = datetime.now(UTC)
_FAST_BCRYPT_COST = 4  # тест-only — bcrypt cost=4 минимизирует CPU


def _settings() -> AuthSettings:
    return AuthSettings(  # pyright: ignore[reportCallIssue]
        bcrypt_cost=_FAST_BCRYPT_COST,
        binding_code_ttl_s=600,
        session_ttl_s=86400,
        cookie_name="bestfiend_session",
        cookie_secure=False,
    )


def _profile(user_id: UUID, *, status: str = "active") -> UserProfile:
    return UserProfile(
        user_id=user_id,
        role="user",
        status=status,  # type: ignore[arg-type]
        created_at=_NOW,
    )


class _UserServiceStub:
    def __init__(self) -> None:
        self._by_id: dict[UUID, UserProfile] = {}

    def seed(self, profile: UserProfile) -> None:
        self._by_id[profile.user_id] = profile

    async def get_by_id(self, user_id: UUID) -> UserProfile:
        profile = self._by_id.get(user_id)
        if profile is None:
            raise AssertionError(f"unexpected user_id={user_id}")
        return profile


class _SessionRepoStub:
    def __init__(self) -> None:
        self.create_calls: list[tuple[UUID, datetime]] = []
        self.deleted: list[UUID] = []
        self._sessions: dict[UUID, SessionRecord] = {}

    async def create(self, *, user_id: UUID, expires_at: datetime) -> SessionRecord:
        self.create_calls.append((user_id, expires_at))
        session_id = uuid4()
        record = SessionRecord(
            session_id=session_id,
            user_id=user_id,
            created_at=_NOW,
            expires_at=expires_at,
        )
        self._sessions[session_id] = record
        return record

    async def get_by_id(self, session_id: UUID) -> SessionRecord | None:
        return self._sessions.get(session_id)

    async def delete_by_id(self, session_id: UUID) -> bool:
        self.deleted.append(session_id)
        return self._sessions.pop(session_id, None) is not None


class _BindingRepoStub:
    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []
        self.deleted: list[str] = []
        self._by_code: dict[str, BindingCodeRecord] = {}

    def seed(self, code: str, user_id: UUID, expires_at: datetime) -> None:
        self._by_code[code] = BindingCodeRecord(
            code=code,
            user_id=user_id,
            expires_at=expires_at,
            created_at=_NOW,
        )

    async def create_or_replace(
        self,
        *,
        code: str,
        user_id: UUID,
        expires_at: datetime,
    ) -> BindingCodeRecord:
        self.created.append(
            {"code": code, "user_id": user_id, "expires_at": expires_at},
        )
        record = BindingCodeRecord(
            code=code,
            user_id=user_id,
            expires_at=expires_at,
            created_at=_NOW,
        )
        self._by_code[code] = record
        return record

    async def get_by_code(self, code: str) -> BindingCodeRecord | None:
        return self._by_code.get(code)

    async def delete_by_code(self, code: str) -> bool:
        self.deleted.append(code)
        return self._by_code.pop(code, None) is not None


class _AuthUserRepoStub:
    def __init__(self) -> None:
        self.set_credentials_calls: list[dict[str, Any]] = []
        self.update_password_calls: list[tuple[UUID, str]] = []
        self._by_login: dict[str, AuthCredentials] = {}
        self._by_user: dict[UUID, str] = {}

    def seed_credentials(self, user_id: UUID, login: str, password_hash: str) -> None:
        self._by_login[login] = AuthCredentials(
            user_id=user_id,
            login=login,
            password_hash=password_hash,
            status="active",
        )
        self._by_user[user_id] = password_hash

    async def get_credentials_by_login(self, login: str) -> AuthCredentials | None:
        return self._by_login.get(login)

    async def set_credentials(
        self,
        *,
        user_id: UUID,
        login: str,
        password_hash: str,
    ) -> None:
        self.set_credentials_calls.append(
            {"user_id": user_id, "login": login, "password_hash": password_hash},
        )
        self._by_login[login] = AuthCredentials(
            user_id=user_id,
            login=login,
            password_hash=password_hash,
            status="active",
        )
        self._by_user[user_id] = password_hash

    async def get_password_hash(self, user_id: UUID) -> str | None:
        return self._by_user.get(user_id)

    async def update_password_hash(
        self,
        *,
        user_id: UUID,
        password_hash: str,
    ) -> None:
        self.update_password_calls.append((user_id, password_hash))
        self._by_user[user_id] = password_hash


def _build_auth_service(
    user_service: _UserServiceStub,
    *,
    session_repo: _SessionRepoStub | None = None,
    binding_repo: _BindingRepoStub | None = None,
    auth_user_repo: _AuthUserRepoStub | None = None,
) -> AuthService:
    return AuthService(
        session_repository=session_repo or _SessionRepoStub(),  # type: ignore[arg-type]
        user_service=user_service,  # type: ignore[arg-type]
        binding_repository=binding_repo or _BindingRepoStub(),  # type: ignore[arg-type]
        auth_user_repository=auth_user_repo or _AuthUserRepoStub(),  # type: ignore[arg-type]
        settings=_settings(),
    )


@pytest.mark.asyncio
async def test_login_returns_session_and_profile() -> None:
    user_id = uuid4()
    users = _UserServiceStub()
    users.seed(_profile(user_id, status="active"))

    auth_repo = _AuthUserRepoStub()
    auth_repo.seed_credentials(
        user_id,
        "alice",
        hash_password("p@ssword", cost=_FAST_BCRYPT_COST),
    )
    sessions = _SessionRepoStub()
    service = _build_auth_service(
        users, session_repo=sessions, auth_user_repo=auth_repo
    )

    session, user = await service.login(login="alice", password="p@ssword")

    assert user.user_id == user_id
    assert session.user_id == user_id
    assert len(sessions.create_calls) == 1


@pytest.mark.asyncio
async def test_login_wrong_password_rejected() -> None:
    user_id = uuid4()
    users = _UserServiceStub()
    users.seed(_profile(user_id))
    auth_repo = _AuthUserRepoStub()
    auth_repo.seed_credentials(
        user_id,
        "alice",
        hash_password("correct", cost=_FAST_BCRYPT_COST),
    )
    service = _build_auth_service(users, auth_user_repo=auth_repo)

    with pytest.raises(InvalidCredentialsError):
        await service.login(login="alice", password="wrong")


@pytest.mark.asyncio
async def test_login_unknown_login_rejected() -> None:
    service = _build_auth_service(_UserServiceStub())

    with pytest.raises(InvalidCredentialsError):
        await service.login(login="ghost", password="anything")


@pytest.mark.asyncio
async def test_login_pending_user_rejected() -> None:
    user_id = uuid4()
    users = _UserServiceStub()
    users.seed(_profile(user_id, status="pending"))
    auth_repo = _AuthUserRepoStub()
    auth_repo.seed_credentials(
        user_id,
        "alice",
        hash_password("pw", cost=_FAST_BCRYPT_COST),
    )
    service = _build_auth_service(users, auth_user_repo=auth_repo)

    with pytest.raises(UserStatusError):
        await service.login(login="alice", password="pw")


@pytest.mark.asyncio
async def test_bind_credentials_creates_session_and_sets_creds() -> None:
    user_id = uuid4()
    users = _UserServiceStub()
    users.seed(_profile(user_id, status="active"))
    binding = _BindingRepoStub()
    binding.seed("123456", user_id, _NOW + timedelta(minutes=10))
    auth_repo = _AuthUserRepoStub()
    sessions = _SessionRepoStub()

    service = _build_auth_service(
        users,
        session_repo=sessions,
        binding_repo=binding,
        auth_user_repo=auth_repo,
    )
    session, user = await service.bind_credentials(
        code="123456",
        login="alice",
        password="strongpass",
    )

    assert user.user_id == user_id
    assert session.user_id == user_id
    assert len(auth_repo.set_credentials_calls) == 1
    assert auth_repo.set_credentials_calls[0]["login"] == "alice"
    assert "123456" in binding.deleted  # код удалён после bind


@pytest.mark.asyncio
async def test_bind_credentials_unknown_code_rejected() -> None:
    users = _UserServiceStub()
    service = _build_auth_service(users)

    with pytest.raises(BindingCodeNotFoundError):
        await service.bind_credentials(code="000000", login="x", password="strongpas")


@pytest.mark.asyncio
async def test_bind_credentials_expired_code_rejected_and_deleted() -> None:
    user_id = uuid4()
    users = _UserServiceStub()
    users.seed(_profile(user_id))
    binding = _BindingRepoStub()
    binding.seed("111111", user_id, _NOW - timedelta(minutes=1))

    service = _build_auth_service(users, binding_repo=binding)

    with pytest.raises(BindingCodeExpiredError):
        await service.bind_credentials(
            code="111111",
            login="x",
            password="strongpas",
        )
    assert "111111" in binding.deleted


@pytest.mark.asyncio
async def test_change_password_success() -> None:
    user_id = uuid4()
    auth_repo = _AuthUserRepoStub()
    auth_repo.seed_credentials(
        user_id,
        "alice",
        hash_password("oldpass", cost=_FAST_BCRYPT_COST),
    )
    service = _build_auth_service(_UserServiceStub(), auth_user_repo=auth_repo)

    await service.change_password(
        user_id=user_id,
        current_password="oldpass",
        new_password="newpass1",
    )

    assert len(auth_repo.update_password_calls) == 1


@pytest.mark.asyncio
async def test_change_password_wrong_current_rejected() -> None:
    user_id = uuid4()
    auth_repo = _AuthUserRepoStub()
    auth_repo.seed_credentials(
        user_id,
        "alice",
        hash_password("oldpass", cost=_FAST_BCRYPT_COST),
    )
    service = _build_auth_service(_UserServiceStub(), auth_user_repo=auth_repo)

    with pytest.raises(InvalidCurrentPasswordError):
        await service.change_password(
            user_id=user_id,
            current_password="wrong",
            new_password="newpass1",
        )


@pytest.mark.asyncio
async def test_change_password_user_without_credentials_rejected() -> None:
    user_id = uuid4()
    service = _build_auth_service(
        _UserServiceStub(), auth_user_repo=_AuthUserRepoStub()
    )

    with pytest.raises(InvalidCurrentPasswordError):
        await service.change_password(
            user_id=user_id,
            current_password="x",
            new_password="newpass1",
        )


@pytest.mark.asyncio
async def test_generate_binding_code_returns_record() -> None:
    user_id = uuid4()
    users = _UserServiceStub()
    users.seed(_profile(user_id, status="active"))
    binding = _BindingRepoStub()
    service = _build_auth_service(users, binding_repo=binding)

    record = await service.generate_binding_code(user_id)

    assert len(binding.created) == 1
    assert record.user_id == user_id
    assert record.code  # 6-значный код, формат проверяется на стороне DB/contract


@pytest.mark.asyncio
async def test_generate_binding_code_pending_user_rejected() -> None:
    user_id = uuid4()
    users = _UserServiceStub()
    users.seed(_profile(user_id, status="pending"))
    service = _build_auth_service(users)

    with pytest.raises(UserStatusError):
        await service.generate_binding_code(user_id)


@pytest.mark.asyncio
async def test_generate_binding_code_retries_on_collision() -> None:
    """`AuthUnavailableError("collision")` → retry до 5 раз."""
    user_id = uuid4()
    users = _UserServiceStub()
    users.seed(_profile(user_id))

    class _CollidingRepo(_BindingRepoStub):
        def __init__(self) -> None:
            super().__init__()
            self.attempts = 0

        async def create_or_replace(
            self,
            *,
            code: str,
            user_id: UUID,
            expires_at: datetime,
        ) -> BindingCodeRecord:
            self.attempts += 1
            if self.attempts < 3:
                raise AuthUnavailableError("binding_code collision on code=X")
            return await super().create_or_replace(
                code=code,
                user_id=user_id,
                expires_at=expires_at,
            )

    binding = _CollidingRepo()
    service = _build_auth_service(users, binding_repo=binding)
    record = await service.generate_binding_code(user_id)
    assert binding.attempts == 3
    assert record.user_id == user_id
