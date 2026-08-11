"""Контракт-тесты ModelRegistry.resolve: default / user context / llm_custom_config / instruction."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest

from bestfiend.control_plane.assistant.models import UserAssistantConfigRecord
from bestfiend.control_plane.model_registry import (
    ModelConfigRecord,
    ModelRegistry,
    ResolveModelRequest,
)
from bestfiend.control_plane.users.models import UserProfile


_NOW = datetime.now(UTC)


def _model_record(model_id: str, config: dict[str, Any]) -> ModelConfigRecord:
    return ModelConfigRecord(
        id=model_id,
        name=model_id,
        config=config,
        created_at=_NOW,
        updated_at=_NOW,
    )


def _user_profile(user_id: UUID) -> UserProfile:
    return UserProfile(
        user_id=user_id,
        role="user",
        status="active",
        telegram_chat_id=None,
        discord_user_id=None,
        login=None,
        timezone="Europe/Belgrade",
        city=None,
        country=None,
        created_at=_NOW,
        updated_at=None,
    )


def _user_config(
    user_id: UUID,
    *,
    user_instruction: str = "",
    llm_custom_config: dict[str, Any] | None = None,
) -> UserAssistantConfigRecord:
    return UserAssistantConfigRecord(
        user_id=user_id,
        user_instruction=user_instruction,
        llm_custom_config=llm_custom_config or {},
        updated_at=_NOW,
    )


class _ModelRepoStub:
    def __init__(self, by_id: dict[str, ModelConfigRecord]) -> None:
        self._by_id = by_id

    async def get_by_id(self, model_id: str) -> ModelConfigRecord:
        return self._by_id[model_id]


class _AssistantRepoStub:
    def __init__(self, record: UserAssistantConfigRecord | None = None) -> None:
        self._record = record

    async def get_by_user(self, user_id: UUID) -> UserAssistantConfigRecord | None:
        return self._record


class _UserRepoStub:
    def __init__(self, profile: UserProfile | None) -> None:
        self._profile = profile

    async def get_by_id(self, user_id: UUID) -> UserProfile | None:
        return self._profile


def _registry(
    *,
    models: dict[str, ModelConfigRecord] | None = None,
    user_config: UserAssistantConfigRecord | None = None,
    profile: UserProfile | None = None,
) -> ModelRegistry:
    return ModelRegistry(
        model_repository=_ModelRepoStub(models or {}),  # type: ignore[arg-type]
        user_config_repository=_AssistantRepoStub(user_config),  # type: ignore[arg-type]
        user_repository=_UserRepoStub(profile),  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
async def test_resolve_without_user_returns_default() -> None:
    reg = _registry(
        models={"m1": _model_record("m1", {"provider": "x", "temperature": 0.5})},
    )
    res = await reg.resolve(ResolveModelRequest(model_id="m1"))
    assert res.config == {"provider": "x", "temperature": 0.5}
    assert res.user_environment is None
    assert res.user_instruction is None


@pytest.mark.asyncio
async def test_resolve_with_user_returns_context() -> None:
    uid = uuid4()
    reg = _registry(
        models={"m1": _model_record("m1", {"provider": "x"})},
        profile=_user_profile(uid),
    )
    res = await reg.resolve(ResolveModelRequest(model_id="m1", user_id=uid))
    assert res.user_environment is not None
    assert res.user_environment.timezone == "Europe/Belgrade"


@pytest.mark.asyncio
async def test_resolve_llm_custom_config_replaces_default() -> None:
    """Непустой llm_custom_config — полная замена дефолта (без мержа)."""
    uid = uuid4()
    reg = _registry(
        models={"m1": _model_record("m1", {"provider": "x", "temperature": 0.5})},
        user_config=_user_config(
            uid, llm_custom_config={"provider": "z", "model": "custom"}
        ),
        profile=_user_profile(uid),
    )
    res = await reg.resolve(ResolveModelRequest(model_id="m1", user_id=uid))
    assert res.config == {"provider": "z", "model": "custom"}


@pytest.mark.asyncio
async def test_resolve_empty_llm_custom_config_uses_default() -> None:
    """Пустой llm_custom_config → дефолт из models."""
    uid = uuid4()
    reg = _registry(
        models={"m1": _model_record("m1", {"provider": "x", "temperature": 0.5})},
        user_config=_user_config(uid, llm_custom_config={}),
        profile=_user_profile(uid),
    )
    res = await reg.resolve(ResolveModelRequest(model_id="m1", user_id=uid))
    assert res.config == {"provider": "x", "temperature": 0.5}


@pytest.mark.asyncio
async def test_resolve_emits_user_instruction() -> None:
    uid = uuid4()
    reg = _registry(
        models={"m1": _model_record("m1", {})},
        user_config=_user_config(uid, user_instruction="be concise"),
        profile=_user_profile(uid),
    )
    res = await reg.resolve(ResolveModelRequest(model_id="m1", user_id=uid))
    assert res.user_instruction == "be concise"


@pytest.mark.asyncio
async def test_resolve_missing_user_returns_default() -> None:
    """`profile is None` (юзер отсутствует) → fail-closed: только config."""
    reg = _registry(
        models={"m1": _model_record("m1", {"provider": "x"})},
        profile=None,  # UserRepository.get_by_id вернёт None
    )
    res = await reg.resolve(ResolveModelRequest(model_id="m1", user_id=uuid4()))
    assert res.config == {"provider": "x"}
    assert res.user_environment is None
