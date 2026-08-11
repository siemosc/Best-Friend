"""UserService writers — identity-creation path.

Покрывает: resolve_or_create_by_telegram (is_new=True/False).
"""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest

from bestfiend.control_plane.users.errors import UserConflictError
from bestfiend.control_plane.users.models import UserProfile
from bestfiend.control_plane.users.service import UserService


_NOW = datetime.now(UTC)


def _profile(user_id: UUID, *, status: str = "active") -> UserProfile:
    return UserProfile(
        user_id=user_id,
        role="user",
        status=status,  # type: ignore[arg-type]
        telegram_chat_id=42,
        created_at=_NOW,
    )


class _RepoStub:
    def __init__(self) -> None:
        self.created_pending_calls = 0
        self.linked_discord: tuple[UUID, str] | None = None
        self._by_telegram: dict[int, UserProfile] = {}
        self._by_discord: dict[str, UserProfile] = {}

    async def get_by_telegram_chat_id(self, chat_id: int) -> UserProfile | None:
        return self._by_telegram.get(chat_id)

    async def create_pending(self, *, telegram_chat_id: int) -> UserProfile:
        self.created_pending_calls += 1
        profile = _profile(uuid4(), status="active")
        # симулируем что telegram_chat_id связан
        bound = UserProfile(
            user_id=profile.user_id,
            role=profile.role,
            status=profile.status,
            telegram_chat_id=telegram_chat_id,
            created_at=_NOW,
        )
        self._by_telegram[telegram_chat_id] = bound
        return bound


class _AssistantStub:
    def __init__(self) -> None:
        self.bootstrap_calls = 0

    async def bootstrap_for_user(self, user_id: UUID) -> Any:
        self.bootstrap_calls += 1
        return None


@pytest.mark.asyncio
async def test_resolve_or_create_new_user_returns_is_new_true() -> None:
    """Первый раз — create_pending + bootstrap, is_new=True."""
    repo = _RepoStub()
    assistant = _AssistantStub()
    svc = UserService(repository=repo, assistant_service=assistant)  # pyright: ignore[reportArgumentType]

    profile, is_new = await svc.resolve_or_create_by_telegram(12345)

    assert is_new is True
    assert profile.telegram_chat_id == 12345
    assert repo.created_pending_calls == 1
    assert assistant.bootstrap_calls == 1


@pytest.mark.asyncio
async def test_resolve_or_create_existing_user_returns_is_new_false() -> None:
    """Existing user — no creation, no bootstrap; is_new=False."""
    repo = _RepoStub()
    existing = _profile(uuid4(), status="active")
    repo._by_telegram[42] = existing
    assistant = _AssistantStub()
    svc = UserService(repository=repo, assistant_service=assistant)  # pyright: ignore[reportArgumentType]

    profile, is_new = await svc.resolve_or_create_by_telegram(42)

    assert is_new is False
    assert profile.user_id == existing.user_id
    assert repo.created_pending_calls == 0
    assert assistant.bootstrap_calls == 0


@pytest.mark.asyncio
async def test_resolve_or_create_recovers_only_from_creation_conflict() -> None:
    """Конфликт конкурентной вставки приводит к повторному чтению профиля."""
    profile = _profile(uuid4())

    class _ConflictingRepo(_RepoStub):
        async def create_pending(self, *, telegram_chat_id: int) -> UserProfile:
            self._by_telegram[telegram_chat_id] = profile
            raise UserConflictError("concurrent insert")

    svc = UserService(repository=_ConflictingRepo())  # pyright: ignore[reportArgumentType]

    resolved, is_new = await svc.resolve_or_create_by_telegram(42)

    assert resolved is profile
    assert is_new is False


@pytest.mark.asyncio
async def test_resolve_or_create_propagates_unexpected_creation_error() -> None:
    """Неожиданная ошибка repository не маскируется повторным чтением."""

    class _FailingRepo(_RepoStub):
        async def create_pending(self, *, telegram_chat_id: int) -> UserProfile:
            raise RuntimeError("database protocol failure")

    svc = UserService(repository=_FailingRepo())  # pyright: ignore[reportArgumentType]

    with pytest.raises(RuntimeError, match="database protocol failure"):
        await svc.resolve_or_create_by_telegram(42)
