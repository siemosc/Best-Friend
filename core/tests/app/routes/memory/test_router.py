"""HTTP/auth-слой фасада памяти: guards, ошибки и сериализация."""

from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from bestfiend.app.http import create_app
from bestfiend.control_plane.auth.errors import InvalidSessionError
from bestfiend.control_plane.settings import AuthSettings
from bestfiend.control_plane.users.models import UserProfile, UserRole
from bestfiend.memory.errors import MemoryDatabaseUnavailableError
from tests.memory.fakes import make_note, make_web_facade_memory_runtime, note_row


_AUTH_COOKIE_NAME = "bestfiend_session"


class _AuthServiceStub:
    """In-memory AuthService с картой session → профиль."""

    def __init__(self) -> None:
        self._sessions: dict[UUID, UserProfile] = {}

    def seed_session(self, profile: UserProfile) -> str:
        """Создаёт сессию для профиля и возвращает cookie."""
        session_id = uuid4()
        self._sessions[session_id] = profile
        return str(session_id)

    async def resolve_session(self, session_id: UUID) -> UserProfile:
        """Возвращает профиль сессии или доменную ошибку."""
        profile = self._sessions.get(session_id)
        if profile is None:
            raise InvalidSessionError(f"session_id={session_id} not found")
        return profile


class _UnavailableDatabaseFake:
    """БД памяти, недоступная на любом запросе."""

    async def fetch(self, *_args: Any) -> list[Any]:
        """Имитирует неподнятый пул соединений."""
        raise MemoryDatabaseUnavailableError("Connection pool не инициализирован")


class _RuntimeStub:
    """Узкий runtime для маршрутов памяти."""

    def __init__(self, auth_service: _AuthServiceStub, memory_runtime: Any) -> None:
        self.auth_service = auth_service
        self.auth_settings = AuthSettings(  # pyright: ignore[reportCallIssue]
            bcrypt_cost=4,
            binding_code_ttl_s=600,
            session_ttl_s=86400,
            cookie_name=_AUTH_COOKIE_NAME,
            cookie_secure=False,
        )
        self.memory_runtime = memory_runtime


def _profile(user_id: UUID, role: UserRole = "user") -> UserProfile:
    """Создаёт активный тестовый профиль."""
    return UserProfile(
        user_id=user_id,
        role=role,
        status="active",
        created_at=datetime(2026, 6, 10, tzinfo=UTC),
    )


def _make_client(auth_service: _AuthServiceStub, memory_runtime: Any) -> TestClient:
    """Создаёт TestClient с тестовым runtime."""
    runtime = _RuntimeStub(auth_service, memory_runtime)
    return TestClient(create_app(cast(Any, runtime)))


def test_self_access_ok() -> None:
    """Пользователь читает собственный контекст памяти."""
    user_id = uuid4()
    auth = _AuthServiceStub()
    cookie = auth.seed_session(_profile(user_id))
    client = _make_client(auth, make_web_facade_memory_runtime())
    client.cookies.set(_AUTH_COOKIE_NAME, cookie)

    with client:
        response = client.get(f"/users/{user_id}/memory/context")

    assert response.status_code == 200
    assert response.json() == {"profile": [], "journal": []}


def test_admin_reads_foreign_memory() -> None:
    """Админ читает память любого пользователя."""
    auth = _AuthServiceStub()
    cookie = auth.seed_session(_profile(uuid4(), role="admin"))
    client = _make_client(auth, make_web_facade_memory_runtime())
    client.cookies.set(_AUTH_COOKIE_NAME, cookie)

    with client:
        response = client.get(f"/users/{uuid4()}/memory/context")

    assert response.status_code == 200


def test_non_admin_foreign_memory_forbidden() -> None:
    """Чужая память для не-админа закрыта."""
    auth = _AuthServiceStub()
    cookie = auth.seed_session(_profile(uuid4()))
    client = _make_client(auth, make_web_facade_memory_runtime())
    client.cookies.set(_AUTH_COOKIE_NAME, cookie)

    with client:
        response = client.get(f"/users/{uuid4()}/memory/context")

    assert response.status_code == 403
    assert response.json()["error_code"] == "AUTH_FORBIDDEN"


def test_no_session_unauthorized() -> None:
    """Запрос без session cookie получает 401."""
    client = _make_client(_AuthServiceStub(), make_web_facade_memory_runtime())

    with client:
        response = client.get(f"/users/{uuid4()}/memory/context")

    assert response.status_code == 401
    assert response.json()["error_code"] == "AUTH_INVALID_SESSION"


def test_memory_database_unavailable_returns_503() -> None:
    """Недоступная БД памяти получает доменный 503."""
    user_id = uuid4()
    auth = _AuthServiceStub()
    cookie = auth.seed_session(_profile(user_id))
    client = _make_client(
        auth, make_web_facade_memory_runtime(db=_UnavailableDatabaseFake())
    )
    client.cookies.set(_AUTH_COOKIE_NAME, cookie)

    with client:
        response = client.get(f"/users/{user_id}/memory/overview")

    assert response.status_code == 503
    assert response.json()["error_code"] == "MEMORY_UNAVAILABLE"


def test_unknown_note_returns_404() -> None:
    """Мутация неизвестной заметки получает доменный 404."""
    user_id = uuid4()
    auth = _AuthServiceStub()
    cookie = auth.seed_session(_profile(user_id))
    client = _make_client(auth, make_web_facade_memory_runtime())
    client.cookies.set(_AUTH_COOKIE_NAME, cookie)

    with client:
        response = client.patch(
            f"/users/{user_id}/memory/notes/{uuid4()}",
            json={"in_journal": True},
        )

    assert response.status_code == 404
    assert response.json()["error_code"] == "NOTE_NOT_FOUND"


class _NotesPageDb:
    """Стаб листинга одной заметки без тегов."""

    def __init__(self, row: dict[str, Any]) -> None:
        self.row = row

    async def fetch(self, query: str, *args: object) -> list[dict[str, Any]]:
        """Возвращает заметку или пустые связи сущностей."""
        if "note_entities" in query and "JOIN entities" in query:
            return []
        return [self.row]

    async def fetch_one(self, query: str, *args: object) -> dict[str, Any]:
        """Возвращает размер страницы."""
        return {"total": 1}


def test_notes_page_serialization_contract() -> None:
    """Листинг сохраняет контракт NoteView и метаданные страницы."""
    user_id = uuid4()
    note = make_note("любит чай", kind="preference", subject="user")
    auth = _AuthServiceStub()
    cookie = auth.seed_session(_profile(user_id))
    client = _make_client(
        auth, make_web_facade_memory_runtime(db=_NotesPageDb(note_row(note)))
    )
    client.cookies.set(_AUTH_COOKIE_NAME, cookie)

    with client:
        response = client.get(f"/users/{user_id}/memory/notes?kinds=preference&limit=5")

    assert response.status_code == 200
    body = response.json()
    assert (body["total"], body["limit"], body["offset"]) == (1, 5, 0)
    [item] = body["items"]
    assert item["id"] == str(note.id)
    assert (item["kind"], item["subject"]) == ("preference", "user")
    assert item["content"] == "любит чай"
    assert item["entities"] == []
    assert set(item) == {
        "id",
        "kind",
        "subject",
        "content",
        "event_time",
        "observed_at",
        "status",
        "pinned",
        "pin_section",
        "in_journal",
        "journal_weight",
        "source_turn_start",
        "source_turn_end",
        "use_count",
        "entities",
    }
