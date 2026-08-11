"""UserRepository admin writers: update_role/update_status/update_profile_fields."""

from datetime import UTC, datetime
import re
from typing import Any
from uuid import UUID, uuid4

import pytest

from bestfiend.control_plane.users.errors import UserUnavailableError
from bestfiend.control_plane.users.repository import UserRepository


_NOW = datetime.now(UTC)
_PROFILE_FIELDS = {"timezone", "city", "country"}
_SET_PATTERN = re.compile(r"(\w+)\s*=\s*\$(\d+)")


def _row(
    user_id: UUID,
    *,
    role: str = "user",
    status: str = "active",
    timezone: str = "Europe/Belgrade",
    city: str | None = None,
    country: str | None = None,
) -> dict[str, Any]:
    return {
        "user_id": user_id,
        "role": role,
        "status": status,
        "telegram_chat_id": None,
        "discord_user_id": None,
        "login": None,
        "timezone": timezone,
        "city": city,
        "country": country,
        "created_at": _NOW,
        "updated_at": _NOW,
    }


class _DBStub:
    """In-memory DB-stub: применяет UPDATE через regex-разбор SET clause."""

    def __init__(self) -> None:
        self._rows: dict[UUID, dict[str, Any]] = {}

    def seed(self, user_id: UUID, **fields: Any) -> None:
        self._rows[user_id] = _row(user_id, **fields)

    async def execute(self, query: str, *args: object) -> str:
        return "UPDATE 1"

    async def fetch(self, query: str, *args: object) -> list[Any]:
        return list(self._rows.values())

    async def fetch_one(self, query: str, *args: object) -> Any:
        user_id = args[0]
        assert isinstance(user_id, UUID)
        row = self._rows.get(user_id)
        if row is None:
            return None
        if "UPDATE users" not in query:
            return dict(row)
        # Применяем SET через regex по позиции placeholder'ов
        for match in _SET_PATTERN.finditer(query):
            field = match.group(1)
            idx = int(match.group(2))
            if field in {"role", "status", "discord_user_id"} | _PROFILE_FIELDS:
                row[field] = args[idx - 1]
        row["updated_at"] = datetime.now(UTC)
        return dict(row)


@pytest.mark.asyncio
async def test_update_role_returns_updated_profile() -> None:
    db = _DBStub()
    user_id = uuid4()
    db.seed(user_id, role="user")
    repo = UserRepository(db)  # type: ignore[arg-type]

    profile = await repo.update_role(user_id, role="admin")

    assert profile.role == "admin"


@pytest.mark.asyncio
async def test_update_status_returns_updated_profile() -> None:
    db = _DBStub()
    user_id = uuid4()
    db.seed(user_id, status="pending")
    repo = UserRepository(db)  # type: ignore[arg-type]

    profile = await repo.update_status(user_id, status="active")

    assert profile.status == "active"


@pytest.mark.asyncio
async def test_update_profile_fields_partial() -> None:
    """Partial update: только timezone, остальные не трогаем."""
    db = _DBStub()
    user_id = uuid4()
    db.seed(user_id, timezone="Europe/Belgrade", city="Belgrade")
    repo = UserRepository(db)  # type: ignore[arg-type]

    profile = await repo.update_profile_fields(user_id, {"timezone": "UTC"})

    assert profile.timezone == "UTC"
    assert profile.city == "Belgrade"


@pytest.mark.asyncio
async def test_update_profile_fields_null_clears() -> None:
    """None в значении → SET NULL."""
    db = _DBStub()
    user_id = uuid4()
    db.seed(user_id, city="Belgrade", country="Serbia")
    repo = UserRepository(db)  # type: ignore[arg-type]

    profile = await repo.update_profile_fields(user_id, {"city": None})

    assert profile.city is None
    assert profile.country == "Serbia"


@pytest.mark.asyncio
async def test_update_profile_fields_empty_returns_existing() -> None:
    """Пустой dict — no-op, возвращает текущий профиль без UPDATE."""
    db = _DBStub()
    user_id = uuid4()
    db.seed(user_id, timezone="UTC")
    repo = UserRepository(db)  # type: ignore[arg-type]

    profile = await repo.update_profile_fields(user_id, {})

    assert profile.timezone == "UTC"


@pytest.mark.asyncio
async def test_update_profile_fields_unknown_field_rejected() -> None:
    """Whitelist: поле вне {timezone, city, country} → ValueError."""
    db = _DBStub()
    user_id = uuid4()
    db.seed(user_id)
    repo = UserRepository(db)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="Unknown field"):
        await repo.update_profile_fields(user_id, {"role": "admin"})


@pytest.mark.asyncio
async def test_update_role_missing_user_raises() -> None:
    """UPDATE без RETURNING-row → UserUnavailableError (404-handling — выше service)."""
    db = _DBStub()
    repo = UserRepository(db)  # type: ignore[arg-type]

    with pytest.raises(UserUnavailableError):
        await repo.update_role(uuid4(), role="admin")
