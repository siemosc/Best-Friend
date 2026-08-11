"""Assistant repository: full reader + writer-набор.

Покрывает: `get_by_user` (reader), `bootstrap` (identity-creation path),
`reset`/`update` (web-админ).
"""

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest

from bestfiend.control_plane.assistant.repository import UserAssistantConfigRepository


_NOW = datetime.now(UTC)


class _DBStub:
    def __init__(self, row: Any = None) -> None:
        self._row = row

    async def fetch_one(self, query: str, *args: object) -> Any:
        return self._row


def _make_row(*, llm_custom_config: Any) -> dict[str, Any]:
    return {
        "user_id": uuid4(),
        "user_instruction": "be concise",
        "llm_custom_config": llm_custom_config,
        "updated_at": _NOW,
    }


def test_assistant_repository_full_writer_set() -> None:
    """Полный writer-набор: get_by_user + bootstrap + reset + update."""
    assert hasattr(UserAssistantConfigRepository, "get_by_user")
    assert hasattr(UserAssistantConfigRepository, "bootstrap")
    assert hasattr(UserAssistantConfigRepository, "reset")
    assert hasattr(UserAssistantConfigRepository, "update")


@pytest.mark.asyncio
async def test_get_by_user_returns_record_when_row_present() -> None:
    row = _make_row(llm_custom_config={})
    repo = UserAssistantConfigRepository(_DBStub(row))  # type: ignore[arg-type]
    record = await repo.get_by_user(uuid4())
    assert record is not None
    assert record.user_id == row["user_id"]
    assert record.user_instruction == "be concise"
    assert record.llm_custom_config == {}


@pytest.mark.asyncio
async def test_get_by_user_returns_none_when_no_row() -> None:
    repo = UserAssistantConfigRepository(_DBStub(None))  # type: ignore[arg-type]
    assert await repo.get_by_user(uuid4()) is None


@pytest.mark.asyncio
async def test_get_by_user_parses_jsonb_llm_custom_config_from_string() -> None:
    """JSONB llm_custom_config приходит строкой → orjson.loads → dict."""
    row = _make_row(llm_custom_config='{"provider": "openai", "model": "x"}')
    repo = UserAssistantConfigRepository(_DBStub(row))  # type: ignore[arg-type]
    record = await repo.get_by_user(uuid4())
    assert record is not None
    assert record.llm_custom_config == {"provider": "openai", "model": "x"}
