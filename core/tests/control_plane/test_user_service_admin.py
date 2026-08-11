"""UserService admin writers: update_profile/update_own_profile/link_discord_by_user_id.

Welcome side-effect отсутствует; service остаётся pure (без telegram-deps).
"""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest

from bestfiend.control_plane.users.errors import (
    SelfEditNotAllowedError,
    UserNotFoundError,
)
from bestfiend.control_plane.users.models import UserProfile
from bestfiend.control_plane.users.service import UserService


_NOW = datetime.now(UTC)


def _profile(
    user_id: UUID,
    *,
    role: str = "user",
    status: str = "active",
    discord_user_id: str | None = None,
) -> UserProfile:
    return UserProfile(
        user_id=user_id,
        role=role,  # type: ignore[arg-type]
        status=status,  # type: ignore[arg-type]
        discord_user_id=discord_user_id,
        created_at=_NOW,
    )


class _RepoStub:
    def __init__(self) -> None:
        self._by_id: dict[UUID, UserProfile] = {}
        self.update_role_calls: list[tuple[UUID, str]] = []
        self.update_status_calls: list[tuple[UUID, str]] = []
        self.link_discord_calls: list[tuple[UUID, str]] = []
        self.update_profile_fields_calls: list[tuple[UUID, dict[str, Any]]] = []

    def seed(self, profile: UserProfile) -> None:
        self._by_id[profile.user_id] = profile

    async def get_by_id(self, user_id: UUID) -> UserProfile | None:
        return self._by_id.get(user_id)

    async def update_role(self, user_id: UUID, *, role: Any) -> UserProfile:
        self.update_role_calls.append((user_id, role))
        current = self._by_id[user_id]
        updated = UserProfile(
            **{**current.model_dump(), "role": role, "updated_at": _NOW},
        )
        self._by_id[user_id] = updated
        return updated

    async def update_status(self, user_id: UUID, *, status: Any) -> UserProfile:
        self.update_status_calls.append((user_id, status))
        current = self._by_id[user_id]
        updated = UserProfile(
            **{**current.model_dump(), "status": status, "updated_at": _NOW},
        )
        self._by_id[user_id] = updated
        return updated

    async def link_discord(
        self,
        user_id: UUID,
        *,
        discord_user_id: str,
    ) -> UserProfile:
        self.link_discord_calls.append((user_id, discord_user_id))
        current = self._by_id[user_id]
        updated = UserProfile(
            **{
                **current.model_dump(),
                "discord_user_id": discord_user_id,
                "updated_at": _NOW,
            },
        )
        self._by_id[user_id] = updated
        return updated

    async def update_profile_fields(
        self,
        user_id: UUID,
        fields: dict[str, Any],
    ) -> UserProfile:
        self.update_profile_fields_calls.append((user_id, fields))
        current = self._by_id[user_id]
        updated = UserProfile(
            **{**current.model_dump(), **fields, "updated_at": _NOW},
        )
        self._by_id[user_id] = updated
        return updated


@pytest.mark.asyncio
async def test_update_profile_changes_role_and_status() -> None:
    repo = _RepoStub()
    user_id = uuid4()
    repo.seed(_profile(user_id, role="user", status="pending"))
    svc = UserService(repository=repo)  # type: ignore[arg-type]

    updated = await svc.update_profile(user_id, role="admin", status="active")

    assert updated.role == "admin"
    assert updated.status == "active"
    assert repo.update_role_calls == [(user_id, "admin")]
    assert repo.update_status_calls == [(user_id, "active")]


@pytest.mark.asyncio
async def test_update_profile_self_edit_role_rejected() -> None:
    """admin не может менять собственные role/status (защита от блокировки)."""
    repo = _RepoStub()
    admin_id = uuid4()
    repo.seed(_profile(admin_id, role="admin"))
    svc = UserService(repository=repo)  # type: ignore[arg-type]

    with pytest.raises(SelfEditNotAllowedError):
        await svc.update_profile(
            admin_id,
            role="user",
            current_user_id=admin_id,
        )


@pytest.mark.asyncio
async def test_update_profile_self_edit_status_rejected() -> None:
    repo = _RepoStub()
    admin_id = uuid4()
    repo.seed(_profile(admin_id, role="admin", status="active"))
    svc = UserService(repository=repo)  # type: ignore[arg-type]

    with pytest.raises(SelfEditNotAllowedError):
        await svc.update_profile(
            admin_id,
            status="banned",
            current_user_id=admin_id,
        )


@pytest.mark.asyncio
async def test_update_profile_self_edit_discord_allowed() -> None:
    """Admin может менять чужой discord_user_id (поле не role/status)."""
    repo = _RepoStub()
    admin_id = uuid4()
    repo.seed(_profile(admin_id, role="admin"))
    svc = UserService(repository=repo)  # type: ignore[arg-type]

    updated = await svc.update_profile(
        admin_id,
        discord_user_id="discord_x",
        current_user_id=admin_id,
    )

    assert updated.discord_user_id == "discord_x"


@pytest.mark.asyncio
async def test_update_profile_no_op_when_no_changes() -> None:
    """role/status/discord_user_id равны текущим — никаких UPDATE."""
    repo = _RepoStub()
    user_id = uuid4()
    repo.seed(_profile(user_id, role="user", status="active"))
    svc = UserService(repository=repo)  # type: ignore[arg-type]

    updated = await svc.update_profile(user_id, role="user", status="active")

    assert updated.user_id == user_id
    assert repo.update_role_calls == []
    assert repo.update_status_calls == []


@pytest.mark.asyncio
async def test_update_profile_missing_user_raises() -> None:
    svc = UserService(repository=_RepoStub())  # type: ignore[arg-type]
    with pytest.raises(UserNotFoundError):
        await svc.update_profile(uuid4(), role="admin")


@pytest.mark.asyncio
async def test_update_own_profile_partial_fields() -> None:
    repo = _RepoStub()
    user_id = uuid4()
    repo.seed(_profile(user_id))
    svc = UserService(repository=repo)  # type: ignore[arg-type]

    await svc.update_own_profile(user_id, {"timezone": "UTC", "city": None})

    assert repo.update_profile_fields_calls == [
        (user_id, {"timezone": "UTC", "city": None}),
    ]


def test_user_service_does_not_import_telegram_gateway_client() -> None:
    """Born-clean (Q1): UserService source не содержит welcome/notify side-effects."""
    import inspect

    from bestfiend.control_plane.users import service as service_module

    source = inspect.getsource(service_module)
    forbidden = (
        "TelegramGatewayClient",
        "telegram_gateway_client",
        "notify_user",
        "_send_welcome",
    )
    for token in forbidden:
        assert token not in source, f"forbidden token leaked into service: {token}"
