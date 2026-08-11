"""Пайплайны памяти: fail-soft веток search, use_count, Observer-триггер в write."""

import asyncio
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

from langchain_core.messages import HumanMessage
import pytest

from bestfiend.memory.budget import ReadBudget
from bestfiend.memory.contracts import WriteTurnRequest
from bestfiend.memory.runtime import MemoryRuntime
from bestfiend.memory.search_pipeline import search
from bestfiend.memory.settings import MemorySettings
from bestfiend.memory.write_pipeline import write
from bestfiend.primitives.background_tasks import BackgroundTaskSupervisor
from tests.memory.fakes import NoteRepositoryFake, make_note


# Бюджет read-раскладки для search-тестов: конкретные числа неважны — ветки
# проверяют fail-soft и use_count, не резку.
_BUDGET = ReadBudget(journal=1_000, profile=500, recall=2_000, log_tail=10_000)


def _background_tasks() -> BackgroundTaskSupervisor:
    """Создать supervisor для одного теста пайплайна."""
    return BackgroundTaskSupervisor()


def _runtime(**overrides: Any) -> MemoryRuntime:
    """MemoryRuntime на моках; отдельные части переопределяются под кейс."""
    parts: dict[str, Any] = {
        "db": AsyncMock(),
        "turns_repository": AsyncMock(),
        "notes_repository": AsyncMock(),
        "entities_repository": AsyncMock(),
        "watermarks_repository": AsyncMock(),
        "ops_repository": AsyncMock(),
        "probes_repository": AsyncMock(),
        "measurements_repository": AsyncMock(),
        "memory_settings": MemorySettings(),
        "model_config_loader": None,
    }
    parts.update(overrides)
    runtime = MemoryRuntime(**parts)
    return runtime


@pytest.mark.asyncio
async def test_search_branch_failure_degrades_to_empty_block() -> None:
    """Сбой ветки журнала/профиля/recall → пустые блоки, log_tail живой."""
    turns_repo = AsyncMock()
    turns_repo.recent_turns.return_value = []
    notes_repo = AsyncMock()
    notes_repo.journal_notes.side_effect = RuntimeError("db down")
    notes_repo.pinned_notes.side_effect = RuntimeError("db down")
    entities_repo = AsyncMock()
    entities_repo.match_in_text.side_effect = RuntimeError("db down")
    db = AsyncMock()
    db.fetch.side_effect = RuntimeError("db down")
    runtime = _runtime(
        db=db,
        turns_repository=turns_repo,
        notes_repository=notes_repo,
        entities_repository=entities_repo,
    )

    context = await search(uuid4(), runtime, "привет", _BUDGET, _background_tasks())

    assert context.journal == ""
    assert context.profile == ""
    assert context.recall == ""
    assert isinstance(context.log_tail[-1], HumanMessage)
    assert context.log_tail[-1].content == "привет"


@pytest.mark.asyncio
async def test_recall_notes_get_use_count_bump(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Вошедшие в recall-блок заметки получают use_count (fire-and-forget)."""
    found = make_note("нашлось в архиве")

    async def fake_recall(**kwargs: Any) -> list[Any]:
        return [found]

    monkeypatch.setattr("bestfiend.memory.search_pipeline.recall_notes", fake_recall)
    turns_repo = AsyncMock()
    turns_repo.recent_turns.return_value = []
    notes = NoteRepositoryFake()
    runtime = _runtime(turns_repository=turns_repo, notes_repository=notes)

    context = await search(
        uuid4(), runtime, "что там было?", _BUDGET, _background_tasks()
    )
    await asyncio.sleep(0)  # даём fire-and-forget задаче исполниться
    await asyncio.sleep(0)

    assert "нашлось в архиве" in context.recall
    assert notes.bumped == [found.id]


@pytest.mark.asyncio
async def test_use_count_bump_failure_is_soft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Сбой инкремента use_count не валит сборку контекста и не теряет recall."""
    found = make_note("нашлось в архиве")

    async def fake_recall(**kwargs: Any) -> list[Any]:
        return [found]

    monkeypatch.setattr("bestfiend.memory.search_pipeline.recall_notes", fake_recall)
    turns_repo = AsyncMock()
    turns_repo.recent_turns.return_value = []
    notes = NoteRepositoryFake()
    notes.bump_fail = True
    runtime = _runtime(turns_repository=turns_repo, notes_repository=notes)

    context = await search(
        uuid4(), runtime, "что там было?", _BUDGET, _background_tasks()
    )  # не бросает
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert "нашлось в архиве" in context.recall
    assert notes.bumped == []


@pytest.mark.asyncio
async def test_write_appends_then_triggers_observer() -> None:
    """write: append_turn выполнен, затем observer.maybe_run того же user."""
    turns_repo = AsyncMock()
    runtime = _runtime(turns_repository=turns_repo)
    observer = AsyncMock()
    runtime.observer = observer
    user_id = uuid4()
    request = WriteTurnRequest(
        request_id="req-1",
        created_at=datetime.now(UTC),
        user_message=[{"type": "human", "data": {"content": "q"}}],
        ai_message=[{"type": "ai", "data": {"content": "a"}}],
        token_count_full=5,
    )

    await write(user_id, request, runtime)

    turns_repo.append_turn.assert_awaited_once()
    observer.maybe_run.assert_awaited_once_with(user_id)


@pytest.mark.asyncio
async def test_write_observer_failure_is_soft() -> None:
    """Сбой Observer не пробрасывается: ход записан, исключение поглощено."""
    turns_repo = AsyncMock()
    runtime = _runtime(turns_repository=turns_repo)
    observer = AsyncMock()
    observer.maybe_run.side_effect = RuntimeError("llm down")
    runtime.observer = observer
    request = WriteTurnRequest(request_id="req-1", created_at=datetime.now(UTC))

    await write(uuid4(), request, runtime)  # не бросает

    turns_repo.append_turn.assert_awaited_once()
