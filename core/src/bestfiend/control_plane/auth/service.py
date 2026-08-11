"""AuthService — бизнес-логика аутентификации control_plane.

Reader: `resolve_session`. Writers: login/bind/change_password/logout/
generate_binding_code. Настройки — `control_plane.settings.AuthSettings`,
cookie-хелперы — `control_plane.cookies`, bcrypt — `control_plane.passwords`.
"""

from datetime import UTC, datetime, timedelta
import secrets
from uuid import UUID

from loguru import logger

from bestfiend.control_plane.auth.errors import (
    AuthUnavailableError,
    BindingCodeExpiredError,
    BindingCodeNotFoundError,
    InvalidCredentialsError,
    InvalidCurrentPasswordError,
    InvalidSessionError,
    UserStatusError,
)
from bestfiend.control_plane.auth.models import (
    BindingCodeRecord,
    SessionRecord,
)
from bestfiend.control_plane.auth.passwords import hash_password, verify_password
from bestfiend.control_plane.auth.repository import (
    AuthUserRepository,
    BindingCodeRepository,
    SessionRepository,
)
from bestfiend.control_plane.settings import AuthSettings
from bestfiend.control_plane.users.errors import UserNotFoundError
from bestfiend.control_plane.users.models import UserProfile
from bestfiend.control_plane.users.service import UserService


_CODE_COLLISION_RETRIES = 5
_CODE_RANGE = 1_000_000  # 6-значный код, 000000..999999


class AuthService:
    """Auth-бизнес-логика: session resolve + login/bind/change_password/binding-codes."""

    __slots__ = (
        "_session_repo",
        "_user_service",
        "_binding_repo",
        "_auth_user_repo",
        "_settings",
    )

    def __init__(
        self,
        *,
        session_repository: SessionRepository,
        user_service: UserService,
        binding_repository: BindingCodeRepository,
        auth_user_repository: AuthUserRepository,
        settings: AuthSettings,
    ) -> None:
        self._session_repo = session_repository
        self._user_service = user_service
        self._binding_repo = binding_repository
        self._auth_user_repo = auth_user_repository
        self._settings = settings

    async def resolve_session(self, session_id: UUID) -> UserProfile:
        """Резолвит сессию в профиль юзера.

        Валидация: существует + не просрочена. Просроченные удаляет на лету.
        """
        session = await self._session_repo.get_by_id(session_id)
        if session is None:
            raise InvalidSessionError(f"session_id={session_id} not found")

        if session.expires_at < datetime.now(UTC):
            await self._session_repo.delete_by_id(session_id)
            raise InvalidSessionError(f"session_id={session_id} expired")

        try:
            return await self._user_service.get_by_id(session.user_id)
        except UserNotFoundError as exc:
            # Осиротевшая сессия (CASCADE должен был удалить, но для страховки)
            await self._session_repo.delete_by_id(session_id)
            raise InvalidSessionError(
                f"session_id={session_id} points to missing user"
            ) from exc

    async def login(
        self,
        *,
        login: str,
        password: str,
    ) -> tuple[SessionRecord, UserProfile]:
        """Проверяет credentials и создаёт сессию."""
        credentials = await self._auth_user_repo.get_credentials_by_login(login)
        if credentials is None:
            raise InvalidCredentialsError("invalid login or password")

        if not verify_password(password, credentials.password_hash):
            raise InvalidCredentialsError("invalid login or password")

        user = await self._user_service.get_by_id(credentials.user_id)
        _ensure_active(user)

        session = await self._new_session(user.user_id)
        logger.info(
            "AuthService: login success user_id={} login={}",
            user.user_id,
            login,
        )
        return session, user

    async def bind_credentials(
        self,
        *,
        code: str,
        login: str,
        password: str,
    ) -> tuple[SessionRecord, UserProfile]:
        """Привязывает login/password к юзеру по коду и создаёт сессию."""
        binding = await self._binding_repo.get_by_code(code)
        if binding is None:
            raise BindingCodeNotFoundError(f"binding code={code} not found")

        if binding.expires_at < datetime.now(UTC):
            await self._binding_repo.delete_by_code(code)
            raise BindingCodeExpiredError(f"binding code={code} expired")

        user = await self._user_service.get_by_id(binding.user_id)
        _ensure_active(user)

        password_hash = hash_password(password, cost=self._settings.bcrypt_cost)
        await self._auth_user_repo.set_credentials(
            user_id=user.user_id,
            login=login,
            password_hash=password_hash,
        )
        await self._binding_repo.delete_by_code(code)

        session = await self._new_session(user.user_id)
        logger.info(
            "AuthService: bind success user_id={} login={}",
            user.user_id,
            login,
        )
        # Перечитываем профиль — set_credentials обновил login поле
        refreshed = await self._user_service.get_by_id(user.user_id)
        return session, refreshed

    async def change_password(
        self,
        *,
        user_id: UUID,
        current_password: str,
        new_password: str,
    ) -> None:
        """Меняет пароль юзера. Требует корректный current_password.

        Другие сессии юзера не инвалидируются — осознанное упрощение для MVP.
        """
        current_hash = await self._auth_user_repo.get_password_hash(user_id)
        if current_hash is None:
            raise InvalidCurrentPasswordError(f"user_id={user_id} has no password set")
        if not verify_password(current_password, current_hash):
            raise InvalidCurrentPasswordError("current password does not match")
        new_hash = hash_password(new_password, cost=self._settings.bcrypt_cost)
        await self._auth_user_repo.update_password_hash(
            user_id=user_id,
            password_hash=new_hash,
        )
        logger.info("AuthService: password changed user_id={}", user_id)

    async def logout(self, session_id: UUID) -> None:
        """Удаляет сессию. No-op если сессии нет."""
        deleted = await self._session_repo.delete_by_id(session_id)
        logger.info(
            "AuthService: logout session_id={} deleted={}",
            session_id,
            deleted,
        )

    async def generate_binding_code(self, user_id: UUID) -> BindingCodeRecord:
        """Генерирует 6-значный код для юзера.

        Требует `status=active`. Повторный вызов заменяет предыдущий код.
        """
        user = await self._user_service.get_by_id(user_id)
        _ensure_active(user)

        expires_at = datetime.now(UTC) + timedelta(
            seconds=self._settings.binding_code_ttl_s,
        )

        for _ in range(_CODE_COLLISION_RETRIES):
            code = _random_6_digit_code()
            try:
                record = await self._binding_repo.create_or_replace(
                    code=code,
                    user_id=user_id,
                    expires_at=expires_at,
                )
            except AuthUnavailableError as exc:
                if "collision" in str(exc):
                    logger.warning("AuthService: code collision, retrying: {}", exc)
                    continue
                raise
            logger.info(
                "AuthService: generated binding code user_id={} expires_at={}",
                user_id,
                expires_at.isoformat(),
            )
            return record

        raise AuthUnavailableError(
            "Failed to generate unique 6-digit binding code after retries"
        )

    async def _new_session(self, user_id: UUID) -> SessionRecord:
        expires_at = datetime.now(UTC) + timedelta(
            seconds=self._settings.session_ttl_s,
        )
        return await self._session_repo.create(
            user_id=user_id,
            expires_at=expires_at,
        )


def _random_6_digit_code() -> str:
    """Криптостойкий 6-значный код с паддингом нулями."""
    return f"{secrets.randbelow(_CODE_RANGE):06d}"


def _ensure_active(user: UserProfile) -> None:
    """Гарантирует что юзер `active`; иначе UserStatusError."""
    if user.status != "active":
        raise UserStatusError(
            f"user_id={user.user_id} status={user.status!r} is not active"
        )
