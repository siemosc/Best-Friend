"""Общие фейки HTTP-маршрутов: сервисы control plane, runtime, TestClient.

Без БД, sync TestClient: проверяется реальная связка router + dependencies +
exception-handlers, фейки лишь подают данные.
"""

from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from bestfiend.app.http import create_app
from bestfiend.control_plane.assistant.models import UserAssistantConfigRecord
from bestfiend.control_plane.auth.errors import (
    InvalidCredentialsError,
    InvalidSessionError,
)
from bestfiend.control_plane.auth.models import (
    BindingCodeRecord,
    SessionRecord,
)
from bestfiend.control_plane.dashboard import DashboardHealthSnapshot
from bestfiend.control_plane.settings import AuthSettings
from bestfiend.control_plane.users.errors import SelfEditNotAllowedError
from bestfiend.control_plane.users.models import (
    UserProfile,
    UserRole,
    UserStatus,
)


AUTH_COOKIE_NAME = "bestfiend_session"
NOW = datetime.now(UTC)

# Точный набор полей UserResponse: контракт байт-в-байт, намеренно без password_hash.
EXPECTED_USER_FIELDS = {
    "user_id",
    "role",
    "status",
    "telegram_chat_id",
    "discord_user_id",
    "login",
    "timezone",
    "city",
    "country",
    "created_at",
    "updated_at",
}


def make_profile(
    *,
    user_id: UUID | None = None,
    role: UserRole = "user",
    status: UserStatus = "active",
    login: str | None = None,
    telegram_chat_id: int | None = None,
) -> UserProfile:
    """Активный профиль пользователя со значениями по умолчанию."""
    return UserProfile(
        user_id=user_id or uuid4(),
        role=role,
        status=status,
        login=login,
        telegram_chat_id=telegram_chat_id,
        created_at=NOW,
    )


class UserServiceFake:
    def __init__(self) -> None:
        self._by_id: dict[UUID, UserProfile] = {}
        self.last_update_profile: dict[str, Any] | None = None
        self.last_update_own: tuple[UUID, dict[str, Any]] | None = None

    def seed(self, profile: UserProfile) -> None:
        self._by_id[profile.user_id] = profile

    async def get_by_id(self, user_id: UUID) -> UserProfile:
        if user_id not in self._by_id:
            from bestfiend.control_plane.users.errors import UserNotFoundError

            raise UserNotFoundError(f"user_id={user_id} not found")
        return self._by_id[user_id]

    async def list_users(self) -> list[UserProfile]:
        return list(self._by_id.values())

    async def update_profile(
        self,
        user_id: UUID,
        *,
        role: Any = None,
        status: Any = None,
        discord_user_id: Any = None,
        current_user_id: UUID | None = None,
    ) -> UserProfile:
        self.last_update_profile = {
            "user_id": user_id,
            "role": role,
            "status": status,
            "discord_user_id": discord_user_id,
            "current_user_id": current_user_id,
        }
        if (
            current_user_id is not None
            and current_user_id == user_id
            and (role is not None or status is not None)
        ):
            raise SelfEditNotAllowedError("cannot self-edit")
        current = self._by_id[user_id]
        updated = UserProfile(
            **{
                **current.model_dump(),
                **({"role": role} if role else {}),
                **({"status": status} if status else {}),
                **({"discord_user_id": discord_user_id} if discord_user_id else {}),
            },
        )
        self._by_id[user_id] = updated
        return updated

    async def update_own_profile(
        self,
        user_id: UUID,
        fields: dict[str, Any],
    ) -> UserProfile:
        self.last_update_own = (user_id, fields)
        current = self._by_id[user_id]
        updated = UserProfile(**{**current.model_dump(), **fields})
        self._by_id[user_id] = updated
        return updated


class AuthServiceFake:
    def __init__(self) -> None:
        self._sessions: dict[UUID, UserProfile] = {}
        self.login_calls: list[tuple[str, str]] = []
        self.bind_calls: list[dict[str, Any]] = []
        self.change_pwd_calls: list[dict[str, Any]] = []
        self.logout_calls: list[UUID] = []
        self.binding_code_calls: list[UUID] = []

    def seed_session(self, profile: UserProfile) -> str:
        sid = uuid4()
        self._sessions[sid] = profile
        return str(sid)

    async def resolve_session(self, session_id: UUID) -> UserProfile:
        if session_id not in self._sessions:
            raise InvalidSessionError(f"session_id={session_id} not found")
        return self._sessions[session_id]

    async def login(
        self,
        *,
        login: str,
        password: str,
    ) -> tuple[SessionRecord, UserProfile]:
        self.login_calls.append((login, password))
        if login != "alice" or password != "correct123":
            raise InvalidCredentialsError("invalid")
        user = make_profile(login=login)
        session_id = uuid4()
        self._sessions[session_id] = user
        return (
            SessionRecord(
                session_id=session_id,
                user_id=user.user_id,
                created_at=NOW,
                expires_at=NOW + timedelta(days=1),
            ),
            user,
        )

    async def bind_credentials(
        self,
        *,
        code: str,
        login: str,
        password: str,
    ) -> tuple[SessionRecord, UserProfile]:
        self.bind_calls.append({"code": code, "login": login})
        user = make_profile(login=login)
        session_id = uuid4()
        self._sessions[session_id] = user
        return (
            SessionRecord(
                session_id=session_id,
                user_id=user.user_id,
                created_at=NOW,
                expires_at=NOW + timedelta(days=1),
            ),
            user,
        )

    async def change_password(
        self,
        *,
        user_id: UUID,
        current_password: str,
        new_password: str,
    ) -> None:
        self.change_pwd_calls.append(
            {
                "user_id": user_id,
                "current_password": current_password,
                "new_password": new_password,
            },
        )

    async def logout(self, session_id: UUID) -> None:
        self.logout_calls.append(session_id)
        self._sessions.pop(session_id, None)

    async def generate_binding_code(self, user_id: UUID) -> BindingCodeRecord:
        self.binding_code_calls.append(user_id)
        return BindingCodeRecord(
            code="654321",
            user_id=user_id,
            expires_at=NOW + timedelta(minutes=10),
            created_at=NOW,
        )


class AssistantServiceFake:
    def __init__(self) -> None:
        self._records: dict[UUID, UserAssistantConfigRecord] = {}
        self.reset_calls: list[UUID] = []
        self.update_calls: list[tuple[UUID, dict[str, Any]]] = []

    def seed(self, user_id: UUID) -> UserAssistantConfigRecord:
        record = UserAssistantConfigRecord(
            user_id=user_id,
            user_instruction="",
            llm_custom_config={},
            updated_at=NOW,
        )
        self._records[user_id] = record
        return record

    async def get_for_user(self, user_id: UUID) -> UserAssistantConfigRecord:
        return self._records.get(user_id) or self.seed(user_id)

    async def reset_to_defaults(self, user_id: UUID) -> UserAssistantConfigRecord:
        self.reset_calls.append(user_id)
        return self.seed(user_id)

    async def update_for_user(
        self,
        user_id: UUID,
        **fields: Any,
    ) -> UserAssistantConfigRecord:
        self.update_calls.append((user_id, fields))
        current = self._records.get(user_id) or self.seed(user_id)
        updated_dict = current.model_dump()
        for key, value in fields.items():
            updated_dict[key] = value
        updated_dict["updated_at"] = datetime.now(UTC)
        updated = UserAssistantConfigRecord(**updated_dict)
        self._records[user_id] = updated
        return updated


class DashboardServiceFake:
    async def snapshot(self) -> DashboardHealthSnapshot:
        from bestfiend.control_plane.dashboard import DashboardLinks, ServiceHealth

        return DashboardHealthSnapshot(
            services=[
                ServiceHealth(
                    name="core",
                    url="http://localhost:8010",
                    status="healthy",
                    latency_ms=5,
                    checked_at=NOW,
                ),
            ],
            links=DashboardLinks(langfuse_url=""),
            fetched_at=NOW,
        )


class RuntimeFake:
    def __init__(
        self,
        *,
        user_service: UserServiceFake | None = None,
        auth_service: AuthServiceFake | None = None,
        assistant_service: AssistantServiceFake | None = None,
        dashboard_service: DashboardServiceFake | None = None,
    ) -> None:
        self.user_service = user_service or UserServiceFake()
        self.auth_service = auth_service or AuthServiceFake()
        self.assistant_service = assistant_service or AssistantServiceFake()
        self.dashboard_service = dashboard_service or DashboardServiceFake()
        self.auth_settings = AuthSettings(  # pyright: ignore[reportCallIssue]
            bcrypt_cost=4,
            binding_code_ttl_s=600,
            session_ttl_s=86400,
            cookie_name=AUTH_COOKIE_NAME,
            cookie_secure=False,
        )


def make_client(runtime: RuntimeFake) -> TestClient:
    return TestClient(create_app(cast(Any, runtime)))


def login_admin(runtime: RuntimeFake, client: TestClient) -> UserProfile:
    admin = make_profile(role="admin", login="admin")
    cookie = runtime.auth_service.seed_session(admin)
    client.cookies.set(AUTH_COOKIE_NAME, cookie)
    return admin
