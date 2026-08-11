"""Сервисный слой assistant-конфигов.

`bootstrap_for_user` (identity-creation) + writer-набор (`reset_to_defaults`,
`update_for_user`) + `get_for_user` с lazy-bootstrap.
"""

from typing import Any
from uuid import UUID

from loguru import logger

from bestfiend.control_plane.assistant.errors import AssistantConfigNotFoundError
from bestfiend.control_plane.assistant.models import UserAssistantConfigRecord
from bestfiend.control_plane.assistant.repository import UserAssistantConfigRepository


class UserAssistantConfigService:
    """Бизнес-логика per-user настроек ассистента."""

    __slots__ = ("_user_repo",)

    def __init__(
        self,
        *,
        user_config_repository: UserAssistantConfigRepository,
    ) -> None:
        self._user_repo = user_config_repository

    async def bootstrap_for_user(self, user_id: UUID) -> UserAssistantConfigRecord:
        """Создаёт пустую запись пользователя (user_instruction='', llm_custom_config={}).

        Идемпотентно: если запись уже существует — возвращает её без изменений.
        """
        record = await self._user_repo.bootstrap(user_id)
        logger.info(
            "UserAssistantConfigService: bootstrap user_id={} done",
            user_id,
        )
        return record

    async def get_for_user(self, user_id: UUID) -> UserAssistantConfigRecord:
        """Возвращает конфиг пользователя.

        Если записи нет — lazy-bootstrap (self-healing).
        """
        existing = await self._user_repo.get_by_user(user_id)
        if existing is not None:
            return existing
        logger.warning(
            "UserAssistantConfigService: config missing for user_id={}, "
            "performing lazy bootstrap",
            user_id,
        )
        return await self._user_repo.bootstrap(user_id)

    async def reset_to_defaults(
        self,
        user_id: UUID,
    ) -> UserAssistantConfigRecord:
        """Сбрасывает к дефолтам: user_instruction → '', llm_custom_config → {}."""
        try:
            record = await self._user_repo.reset(user_id)
        except AssistantConfigNotFoundError:
            logger.warning(
                "UserAssistantConfigService: reset on missing config user_id={}, "
                "bootstrapping first",
                user_id,
            )
            return await self._user_repo.bootstrap(user_id)
        logger.info(
            "UserAssistantConfigService: reset to defaults user_id={}",
            user_id,
        )
        return record

    async def update_for_user(
        self,
        user_id: UUID,
        **fields: Any,
    ) -> UserAssistantConfigRecord:
        """Частичное обновление конфига пользователя."""
        try:
            return await self._user_repo.update(user_id, **fields)
        except AssistantConfigNotFoundError:
            logger.warning(
                "UserAssistantConfigService: update on missing config user_id={}, "
                "bootstrapping first",
                user_id,
            )
            await self._user_repo.bootstrap(user_id)
            return await self._user_repo.update(user_id, **fields)
