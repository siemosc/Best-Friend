"""Сервисный слой users.

Reader-slice: `get_by_id`, `list_users`.
Identity-creation writers: `resolve_or_create_by_telegram` возвращает
`(profile, is_new)`; welcome-side-effect — ответственность telegram-bot.
Admin writers: `update_profile`/`update_own_profile` без welcome side-effect
(юзер получает inline-welcome при первом сообщении).
"""

from uuid import UUID

from loguru import logger

from bestfiend.control_plane.assistant.errors import AssistantConfigError
from bestfiend.control_plane.assistant.service import UserAssistantConfigService
from bestfiend.control_plane.users.errors import (
    SelfEditNotAllowedError,
    UserConflictError,
    UserNotFoundError,
)
from bestfiend.control_plane.users.models import (
    UserProfile,
    UserRole,
    UserStatus,
)
from bestfiend.control_plane.users.repository import UserRepository


class UserService:
    """Бизнес-логика users.

    Writers (`assistant_service`) — опциональные; тесты могут
    конструировать минимально (только `repository`) для reader-операций.
    """

    __slots__ = ("_repo", "_assistant_service")

    def __init__(
        self,
        *,
        repository: UserRepository,
        assistant_service: UserAssistantConfigService | None = None,
    ) -> None:
        self._repo = repository
        self._assistant_service = assistant_service

    async def get_by_id(self, user_id: UUID) -> UserProfile:
        """Возвращает профиль по user_id или бросает UserNotFoundError."""
        profile = await self._repo.get_by_id(user_id)
        if profile is None:
            raise UserNotFoundError(f"user_id={user_id} not found")
        return profile

    async def list_users(self) -> list[UserProfile]:
        """Возвращает всех пользователей."""
        return await self._repo.list_all()

    # ── Identity-creation writers ──────────────────────────────────────

    async def resolve_or_create_by_telegram(
        self,
        telegram_chat_id: int,
    ) -> tuple[UserProfile, bool]:
        """Возвращает (profile, is_new). Создаёт pending при первом обращении.

        При создании нового user'а — bootstrap assistant_config.
        Если bootstrap упал — logger.error, user всё равно создан.

        Welcome-сообщение — ответственность caller'а (telegram-bot шлёт inline
        если is_new=True).
        """
        existing = await self._repo.get_by_telegram_chat_id(telegram_chat_id)
        if existing is not None:
            return existing, False

        try:
            created = await self._repo.create_pending(
                telegram_chat_id=telegram_chat_id,
            )
        except UserConflictError:
            # Race на UNIQUE(telegram_chat_id) — перечитываем, чей-то insert.
            fallback = await self._repo.get_by_telegram_chat_id(telegram_chat_id)
            if fallback is not None:
                logger.info(
                    "UserService: race on create, re-fetched user_id={}",
                    fallback.user_id,
                )
                return fallback, False
            raise

        logger.info(
            "UserService: created pending user_id={} telegram_chat_id={}",
            created.user_id,
            telegram_chat_id,
        )

        if self._assistant_service is not None:
            try:
                await self._assistant_service.bootstrap_for_user(created.user_id)
            except AssistantConfigError as exc:
                logger.error(
                    "UserService: assistant_config bootstrap failed user_id={}: {}",
                    created.user_id,
                    exc,
                )

        return created, True

    # ── Admin writers (без welcome side-effect) ────────────────────────

    async def update_profile(
        self,
        user_id: UUID,
        *,
        role: UserRole | None = None,
        status: UserStatus | None = None,
        discord_user_id: str | None = None,
        current_user_id: UUID | None = None,
    ) -> UserProfile:
        """Админское обновление: role / status / discord_user_id.

        Если `current_user_id == user_id` и меняется role/status — бросает
        `SelfEditNotAllowedError`, чтобы admin не заблокировал сам себя.
        Welcome-сообщение при активации НЕ отправляется: юзер получит
        inline-welcome из telegram on_text при первом сообщении.
        """
        is_self_edit = current_user_id is not None and current_user_id == user_id
        if is_self_edit and (role is not None or status is not None):
            raise SelfEditNotAllowedError("admin cannot change own role or status")

        current = await self.get_by_id(user_id)
        updated = await self._update_role_if_changed(user_id, current, role)
        updated = await self._update_status_if_changed(user_id, updated, status)
        updated = await self._update_discord_if_changed(
            user_id,
            updated,
            discord_user_id,
        )
        return updated

    async def update_own_profile(
        self,
        user_id: UUID,
        fields: dict[str, str | None],
    ) -> UserProfile:
        """Частичное обновление полей профиля самим юзером.

        `fields` — словарь с реально переданными полями (`exclude_unset` на
        стороне API). `None` в значении означает явную очистку поля (SET NULL).
        """
        await self.get_by_id(user_id)
        return await self._repo.update_profile_fields(user_id, fields)

    async def _update_role_if_changed(
        self,
        user_id: UUID,
        profile: UserProfile,
        role: UserRole | None,
    ) -> UserProfile:
        """Обновляет роль только при реальном изменении."""
        if role is None or role == profile.role:
            return profile
        return await self._repo.update_role(user_id, role=role)

    async def _update_status_if_changed(
        self,
        user_id: UUID,
        profile: UserProfile,
        status: UserStatus | None,
    ) -> UserProfile:
        """Обновляет статус только при реальном изменении."""
        if status is None or status == profile.status:
            return profile
        return await self._repo.update_status(user_id, status=status)

    async def _update_discord_if_changed(
        self,
        user_id: UUID,
        profile: UserProfile,
        discord_user_id: str | None,
    ) -> UserProfile:
        """Обновляет Discord-привязку только при реальном изменении."""
        if discord_user_id is None or discord_user_id == profile.discord_user_id:
            return profile
        return await self._repo.link_discord(
            user_id,
            discord_user_id=discord_user_id,
        )
