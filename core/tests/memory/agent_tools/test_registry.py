"""Тулзы памяти: контракты схем, проброс в recall, транзакционные записи с ops-логом."""

import dataclasses
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from bestfiend.memory.agent_tools import (
    MEMORY_READ_LOG_NAME,
    MEMORY_REVISE_NAME,
    MEMORY_SAVE_NAME,
    MEMORY_SEARCH_NAME,
    MEMORY_TOOL_NAMES,
    build_memory_tools,
)
from bestfiend.memory.settings import MemorySettings
from tests.memory.fakes import (
    NoteRepositoryFake,
    OperationLogRepositoryFake,
    TransactionalDatabaseFake,
    make_agent_tools_runtime,
    make_note,
    make_turn,
)


def test_memory_search_schema_exposes_contract_fields() -> None:
    """Схема memory_search несёт query + kinds + subjects + ограниченный limit."""
    tools = build_memory_tools(make_agent_tools_runtime(), uuid4())
    schema = tools[MEMORY_SEARCH_NAME].args_schema
    assert schema is not None
    fields = schema.model_fields  # type: ignore[union-attr]

    assert set(fields) == {"query", "kinds", "subjects", "limit"}
    limit_meta = {type(m).__name__: m for m in fields["limit"].metadata}
    assert limit_meta["Ge"].ge == 1  # type: ignore[attr-defined]
    assert limit_meta["Le"].le == 20  # type: ignore[attr-defined]


def test_memory_revise_in_toolset_with_contract_fields() -> None:
    """memory_revise входит в набор top-level тулзов и несёт контрактные поля."""
    tools = build_memory_tools(make_agent_tools_runtime(), uuid4())
    assert MEMORY_REVISE_NAME in MEMORY_TOOL_NAMES
    schema = tools[MEMORY_REVISE_NAME].args_schema
    assert schema is not None
    assert set(schema.model_fields) == {  # type: ignore[union-attr]
        "statement_to_replace",
        "corrected_statement",
        "kind",
        "subject",
    }


@pytest.mark.asyncio
async def test_memory_search_passes_kinds_and_limit_to_recall(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """kinds/subjects/limit уезжают в recall_notes (фильтры до ранжирования)."""
    captured: dict[str, Any] = {}

    async def fake_recall(**kwargs: Any) -> list[Any]:
        captured.update(kwargs)
        return []

    monkeypatch.setattr(
        "bestfiend.memory.agent_tools.handlers.recall_notes", fake_recall
    )
    user_id = uuid4()
    tools = build_memory_tools(make_agent_tools_runtime(), user_id)

    result = await tools[MEMORY_SEARCH_NAME].coroutine(  # type: ignore[misc]
        query="что решили про хранилище?",
        kinds=["fact"],
        subjects=["world"],
        limit=3,
    )

    assert captured["kinds"] == ["fact"]
    assert captured["subjects"] == ["world"]
    assert captured["top_k"] == 3
    assert captured["user_id"] == user_id
    assert "Ничего не найдено" in result


@pytest.mark.asyncio
async def test_memory_save_pin_passthrough_with_ops_and_budget() -> None:
    """memory_save: pin кладёт секцию + ops add(detail) + бюджет; без pin — без секции."""
    notes = NoteRepositoryFake()
    ops = OperationLogRepositoryFake()
    db = TransactionalDatabaseFake()
    tools = build_memory_tools(make_agent_tools_runtime(notes, ops, db), uuid4())

    await tools[MEMORY_SAVE_NAME].coroutine(  # type: ignore[misc]
        content="любит краткость",
        kind="preference",
        subject="user",
        pin=True,
        pin_section="preferences",
    )
    await tools[MEMORY_SAVE_NAME].coroutine(  # type: ignore[misc]
        content="обычный факт",
        kind="fact",
        subject="world",
        pin=False,
        pin_section="identity",
    )

    first, second = notes.inserted
    assert first.pinned is True
    assert first.pin_section == "preferences"
    assert second.pinned is False
    assert second.pin_section is None
    assert second.subject == "world"  # модельный субъект доезжает до драфта
    add_ops = ops.logged_ops("add")
    assert [op.pipeline for op in add_ops] == ["tool", "tool"]
    assert add_ops[0].detail == "pin=preferences"
    assert add_ops[1].detail is None
    # Бюджет профиля проверялся только на pin-пути, в той же транзакции.
    assert notes.pinned_executors == [db.transactions[0]]
    assert all(tx.committed for tx in db.transactions)


@pytest.mark.asyncio
async def test_memory_revise_supersedes_with_inheritance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Уверенный матч → supersede: kind/subject/pin/теги наследуются, ops revise с парой id."""
    inherited_entity = uuid4()
    target = make_note(
        "отвечает развёрнуто",
        kind="preference",
        subject="user",
        pinned=True,
        pin_section="preferences",
    )
    notes = NoteRepositoryFake(entity_tags={target.id: [inherited_entity]})
    ops = OperationLogRepositoryFake()
    db = TransactionalDatabaseFake()

    async def fake_resolve(**kwargs: Any) -> Any:
        return target

    monkeypatch.setattr(
        "bestfiend.memory.agent_tools.handlers.resolve_note_by_statement", fake_resolve
    )
    tools = build_memory_tools(make_agent_tools_runtime(notes, ops, db), uuid4())

    result = await tools[MEMORY_REVISE_NAME].coroutine(  # type: ignore[misc]
        statement_to_replace="отвечает развёрнуто",
        corrected_statement="отвечает кратко",
        kind="fact",  # игнорируется: kind наследуется от заменяемой
        subject="world",  # игнорируется: subject наследуется от заменяемой
    )

    [(draft, new_id)] = notes.inserted_with_ids
    assert draft.kind == "preference"  # унаследован, параметр kind проигнорирован
    assert draft.subject == "user"  # унаследован, параметр subject проигнорирован
    assert draft.pinned is True
    assert draft.pin_section == "preferences"
    assert draft.entity_ids == (inherited_entity,)
    assert notes.superseded == [(target.id, new_id)]
    tx = db.transactions[0]
    assert notes.insert_executors == [tx]
    assert notes.supersede_executors == [tx]
    revise_ops = ops.logged_ops("revise")
    assert revise_ops[0].note_id == new_id
    assert revise_ops[0].target_note_id == target.id
    assert "Было" in result


@pytest.mark.asyncio
async def test_memory_search_renders_source_span(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Выдача memory_search несёт span ходов-источников (мост к memory_read_log)."""
    spanned = dataclasses.replace(
        make_note("факт со сценой"), source_turn_start=12, source_turn_end=18
    )
    plain = make_note("факт без провенанса")

    async def fake_recall(**kwargs: Any) -> list[Any]:
        return [spanned, plain]

    monkeypatch.setattr(
        "bestfiend.memory.agent_tools.handlers.recall_notes", fake_recall
    )
    tools = build_memory_tools(make_agent_tools_runtime(), uuid4())

    result = await tools[MEMORY_SEARCH_NAME].coroutine(  # type: ignore[misc]
        query="что там было?"
    )

    assert "(ходы 12–18)" in result
    assert "факт без провенанса" in result
    assert result.count("(ходы") == 1  # без span — без суффикса


@pytest.mark.asyncio
async def test_memory_read_log_renders_range_and_caps() -> None:
    """memory_read_log: диапазон рендерится; cap режет с подсказкой продолжения."""
    turns_repo = AsyncMock()
    turns_repo.turns_range.return_value = [make_turn(5), make_turn(6)]
    runtime = make_agent_tools_runtime(settings=MemorySettings(read_log_max_turns=2))
    runtime.turns_repository = turns_repo
    tools = build_memory_tools(runtime, uuid4())

    result = await tools[MEMORY_READ_LOG_NAME].coroutine(  # type: ignore[misc]
        from_turn=5, to_turn=9
    )

    assert MEMORY_READ_LOG_NAME in MEMORY_TOOL_NAMES
    turns_repo.turns_range.assert_awaited_once()
    assert turns_repo.turns_range.await_args.kwargs["cap"] == 2
    assert "Ход 5" in result
    assert "Ход 6" in result
    assert "from_turn=7" in result  # cap сработал — подсказка продолжения


@pytest.mark.asyncio
async def test_memory_read_log_empty_range() -> None:
    """Пустой диапазон → честный ответ без рендера."""
    turns_repo = AsyncMock()
    turns_repo.turns_range.return_value = []
    runtime = make_agent_tools_runtime()
    runtime.turns_repository = turns_repo
    tools = build_memory_tools(runtime, uuid4())

    result = await tools[MEMORY_READ_LOG_NAME].coroutine(  # type: ignore[misc]
        from_turn=100, to_turn=120
    )

    assert "нет ходов" in result


@pytest.mark.asyncio
async def test_memory_revise_no_match_adds_with_given_kind(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Нет уверенного матча → новая запись с переданным kind и честный ответ."""
    notes = NoteRepositoryFake()
    ops = OperationLogRepositoryFake()

    async def fake_resolve(**kwargs: Any) -> None:
        return None

    monkeypatch.setattr(
        "bestfiend.memory.agent_tools.handlers.resolve_note_by_statement", fake_resolve
    )
    tools = build_memory_tools(make_agent_tools_runtime(notes, ops), uuid4())

    result = await tools[MEMORY_REVISE_NAME].coroutine(  # type: ignore[misc]
        statement_to_replace="что-то выдуманное",
        corrected_statement="пьёт зелёный чай",
        kind="preference",
        subject="user",
    )

    [draft] = notes.inserted
    assert draft.kind == "preference"
    assert draft.subject == "user"  # переданный субъект ушёл в новую запись
    assert draft.pinned is False
    assert notes.superseded == []
    revise_ops = ops.logged_ops("revise")
    assert revise_ops[0].target_note_id is None
    assert "не нашёл" in result
