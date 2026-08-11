"""AssistantConfig writers: reset_to_defaults / update_for_user + lazy-bootstrap."""

from datetime import UTC, datetime
import re
from typing import Any
from uuid import UUID, uuid4

import orjson
import pytest

from bestfiend.control_plane.assistant.repository import UserAssistantConfigRepository
from bestfiend.control_plane.assistant.service import UserAssistantConfigService


_SET_PATTERN = re.compile(r"(\w+)\s*=\s*\$(\d+)")
_UPDATABLE = {"user_instruction", "llm_custom_config"}


_NOW = datetime.now(UTC)


class _DBStub:
    """In-memory user_assistant_configs."""

    def __init__(self) -> None:
        self._rows: dict[UUID, dict[str, Any]] = {}

    def _empty(self, user_id: UUID) -> dict[str, Any]:
        return {
            "user_id": user_id,
            "user_instruction": "",
            "llm_custom_config": {},
            "updated_at": _NOW,
        }

    async def execute(self, query: str, *args: object) -> str:
        if "INSERT INTO user_assistant_configs" in query:
            user_id = args[0]
            assert isinstance(user_id, UUID)
            if user_id not in self._rows:
                self._rows[user_id] = self._empty(user_id)
        return "OK"

    async def fetch(self, query: str, *args: object) -> list[Any]:
        return list(self._rows.values())

    async def fetch_one(self, query: str, *args: object) -> Any:
        if "UPDATE user_assistant_configs" not in query:
            user_id = args[0]
            assert isinstance(user_id, UUID)
            return self._rows.get(user_id)
        user_id = args[0]
        assert isinstance(user_id, UUID)
        row = self._rows.get(user_id)
        if row is None:
            return None
        if "llm_custom_config = '{}'" in query:
            # reset: всё в defaults
            row.update(user_instruction="", llm_custom_config={})
        else:
            # update: применяем по позиции аргументов (за user_id).
            # SET содержит `{field} = $N::jsonb` для llm_custom_config или `{field} = $N`.
            for match in _SET_PATTERN.finditer(query):
                field = match.group(1)
                idx = int(match.group(2))
                if field in _UPDATABLE:
                    value = args[idx - 1]
                    # llm_custom_config приходит JSON-строкой через ::jsonb-cast в SQL
                    if field == "llm_custom_config" and isinstance(value, str):
                        value = orjson.loads(value)
                    row[field] = value
        row["updated_at"] = datetime.now(UTC)
        return row


@pytest.mark.asyncio
async def test_reset_clears_instruction_and_config() -> None:
    db = _DBStub()
    user_id = uuid4()
    db._rows[user_id] = db._empty(user_id)
    db._rows[user_id]["user_instruction"] = "custom"
    db._rows[user_id]["llm_custom_config"] = {"provider": "openai", "model": "x"}

    repo = UserAssistantConfigRepository(db)  # type: ignore[arg-type]
    record = await repo.reset(user_id)

    assert record.user_instruction == ""
    assert record.llm_custom_config == {}


@pytest.mark.asyncio
async def test_update_partial_instruction() -> None:
    db = _DBStub()
    user_id = uuid4()
    db._rows[user_id] = db._empty(user_id)
    repo = UserAssistantConfigRepository(db)  # type: ignore[arg-type]

    record = await repo.update(user_id, user_instruction="be concise")

    assert record.user_instruction == "be concise"


@pytest.mark.asyncio
async def test_update_llm_custom_config_through_jsonb() -> None:
    db = _DBStub()
    user_id = uuid4()
    db._rows[user_id] = db._empty(user_id)
    repo = UserAssistantConfigRepository(db)  # type: ignore[arg-type]

    record = await repo.update(
        user_id,
        llm_custom_config={"provider": "openai", "model": "x"},
    )

    assert record.llm_custom_config == {"provider": "openai", "model": "x"}


@pytest.mark.asyncio
async def test_update_unknown_field_rejected() -> None:
    db = _DBStub()
    user_id = uuid4()
    db._rows[user_id] = db._empty(user_id)
    repo = UserAssistantConfigRepository(db)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="Unknown field"):
        await repo.update(user_id, weird_field="x")


@pytest.mark.asyncio
async def test_service_reset_lazy_bootstrap_when_missing() -> None:
    """reset на отсутствующей записи → bootstrap, не ошибка."""
    db = _DBStub()
    repo = UserAssistantConfigRepository(db)  # type: ignore[arg-type]
    service = UserAssistantConfigService(user_config_repository=repo)

    record = await service.reset_to_defaults(uuid4())

    assert record.user_instruction == ""


@pytest.mark.asyncio
async def test_service_update_lazy_bootstrap_when_missing() -> None:
    """update на отсутствующей записи → bootstrap + retry update."""
    db = _DBStub()
    repo = UserAssistantConfigRepository(db)  # type: ignore[arg-type]
    service = UserAssistantConfigService(user_config_repository=repo)

    record = await service.update_for_user(uuid4(), user_instruction="x")

    assert record.user_instruction == "x"


@pytest.mark.asyncio
async def test_service_get_for_user_lazy_bootstrap() -> None:
    """get_for_user lazy-bootstrap если запись отсутствует."""
    db = _DBStub()
    repo = UserAssistantConfigRepository(db)  # type: ignore[arg-type]
    service = UserAssistantConfigService(user_config_repository=repo)

    user_id = uuid4()
    record = await service.get_for_user(user_id)

    assert record.user_id == user_id
    assert record.user_instruction == ""
