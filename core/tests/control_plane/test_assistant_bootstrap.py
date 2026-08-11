"""UserAssistantConfigService.bootstrap_for_user + repository.bootstrap."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest

from bestfiend.control_plane.assistant.repository import UserAssistantConfigRepository
from bestfiend.control_plane.assistant.service import UserAssistantConfigService


_NOW = datetime.now(UTC)


class _DBStub:
    """In-memory DB stub: INSERT/SELECT для user_assistant_configs."""

    def __init__(self) -> None:
        self._rows: dict[UUID, dict[str, Any]] = {}
        self.execute_calls = 0
        self.fetch_calls = 0

    async def execute(self, query: str, *args: object) -> None:
        self.execute_calls += 1
        # Симулируем INSERT ... ON CONFLICT DO NOTHING
        user_id = args[0]
        assert isinstance(user_id, UUID)
        if user_id not in self._rows:
            self._rows[user_id] = {
                "user_id": user_id,
                "user_instruction": "",
                "llm_custom_config": {},
                "updated_at": _NOW,
            }

    async def fetch_one(self, query: str, *args: object) -> Any:
        self.fetch_calls += 1
        user_id = args[0]
        assert isinstance(user_id, UUID)
        return self._rows.get(user_id)


@pytest.mark.asyncio
async def test_bootstrap_creates_empty_record() -> None:
    db = _DBStub()
    repo = UserAssistantConfigRepository(db)  # type: ignore[arg-type]
    user_id = uuid4()

    record = await repo.bootstrap(user_id)

    assert record.user_id == user_id
    assert record.llm_custom_config == {}
    assert record.user_instruction == ""
    assert db.execute_calls == 1


@pytest.mark.asyncio
async def test_bootstrap_idempotent_on_conflict() -> None:
    """Двойной bootstrap — same row, no exception."""
    db = _DBStub()
    repo = UserAssistantConfigRepository(db)  # type: ignore[arg-type]
    user_id = uuid4()

    record1 = await repo.bootstrap(user_id)
    record2 = await repo.bootstrap(user_id)

    assert record1.user_id == record2.user_id
    assert (
        db.execute_calls == 2
    )  # обе попытки выполнились (ON CONFLICT DO NOTHING на стороне БД)


@pytest.mark.asyncio
async def test_assistant_service_bootstrap_delegates_to_repo() -> None:
    db = _DBStub()
    repo = UserAssistantConfigRepository(db)  # type: ignore[arg-type]
    service = UserAssistantConfigService(user_config_repository=repo)
    user_id = uuid4()

    record = await service.bootstrap_for_user(user_id)

    assert record.user_id == user_id
    assert db.execute_calls == 1
