"""Сервис model_registry: resolve конфига единой модели графа (+ user llm_custom_config).

Reader-only capability: конфиг модели + per-user llm_custom_config/instruction +
user_environment (timezone/city/country) через `UserRepository.get_by_id`.
"""

from typing import Any
from uuid import UUID

from bestfiend.contracts.user_environment import UserEnvironment
from bestfiend.control_plane.assistant.repository import UserAssistantConfigRepository
from bestfiend.control_plane.model_registry.contracts import (
    ResolveModelRequest,
    ResolveModelResponse,
)
from bestfiend.control_plane.model_registry.repository import ModelConfigRepository
from bestfiend.control_plane.users.repository import UserRepository


class ModelRegistry:
    """Резолвит конфиг единой модели графа + per-user llm_custom_config/instruction.

    Чистый ридер: модель из model_repository, пользовательский контекст из
    user_config_repository + user_repository (fail-closed на отсутствующего юзера).
    """

    __slots__ = (
        "_model_repo",
        "_user_config_repo",
        "_user_repo",
    )

    def __init__(
        self,
        *,
        model_repository: ModelConfigRepository,
        user_config_repository: UserAssistantConfigRepository,
        user_repository: UserRepository,
    ) -> None:
        self._model_repo = model_repository
        self._user_config_repo = user_config_repository
        self._user_repo = user_repository

    async def resolve(
        self,
        request: ResolveModelRequest,
    ) -> ResolveModelResponse:
        """Резолвит конфиг единой модели графа (+ user llm_custom_config/instruction)."""
        record = await self._model_repo.get_by_id(request.model_id)
        default = dict(record.config)

        if request.user_id is None:
            return ResolveModelResponse(config=default)

        return await self._resolve_with_user(default=default, user_id=request.user_id)

    async def _resolve_with_user(
        self,
        *,
        default: dict[str, Any],
        user_id: UUID,
    ) -> ResolveModelResponse:
        """Дефолтный конфиг + user llm_custom_config (полная замена) + instruction."""
        user_config = await self._user_config_repo.get_by_user(user_id)
        profile = await self._user_repo.get_by_id(user_id)
        # UserRepository.get_by_id возвращает Optional — fail-closed на
        # отсутствующего юзера: возвращаем default без user_environment.
        if profile is None:
            return ResolveModelResponse(config=default)

        # Непустой llm_custom_config — полная замена дефолта; пустой → дефолт.
        custom = user_config.llm_custom_config if user_config else {}
        config = dict(custom) if custom else default
        instruction = (user_config.user_instruction if user_config else "") or None

        return ResolveModelResponse(
            config=config,
            user_instruction=instruction,
            user_environment=UserEnvironment(
                timezone=profile.timezone,
                city=profile.city,
                country=profile.country,
            ),
        )
