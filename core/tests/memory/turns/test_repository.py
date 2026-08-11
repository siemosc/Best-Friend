"""Тесты TurnRepository: append_turn / recent_turns / turns_after / unprocessed_token_sum."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import asyncpg
import orjson
import pytest

from bestfiend.memory.turns.repository import TurnRepository


@pytest.mark.asyncio
async def test_append_turn_sql_and_args() -> None:
    """INSERT с ON CONFLICT DO NOTHING; jsonb-колонки сериализованы; порядок args."""
    db = AsyncMock()
    repo = TurnRepository(db)
    user_id = uuid4()
    created = datetime.now(UTC)
    user_msg = [{"type": "human", "data": {"content": "q"}}]

    await repo.append_turn(
        user_id=user_id,
        request_id="req-1",
        user_message=user_msg,
        react_loop=[],
        ai_message=[{"type": "ai", "data": {"content": "a"}}],
        token_count_full=10,
        token_count_loop=0,
        created_at=created,
    )

    db.execute.assert_awaited_once()
    args = db.execute.call_args.args
    sql = args[0]
    assert "INSERT INTO turns" in sql
    assert "$3::jsonb" in sql
    assert "ON CONFLICT (user_id, request_id) DO NOTHING" in sql
    assert args[1] == user_id
    assert args[2] == "req-1"
    assert orjson.loads(args[3]) == user_msg
    assert args[6] == 10  # token_count_full
    assert args[8] == created


@pytest.mark.asyncio
async def test_append_turn_raises_on_pg_error() -> None:
    """Сбой PostgreSQL пробрасывается наружу (caller решает)."""
    db = AsyncMock()
    db.execute.side_effect = asyncpg.PostgresError("boom")
    repo = TurnRepository(db)

    with pytest.raises(asyncpg.PostgresError):
        await repo.append_turn(
            user_id=uuid4(),
            request_id="r",
            user_message=[],
            react_loop=[],
            ai_message=[],
            token_count_full=0,
            token_count_loop=0,
            created_at=datetime.now(UTC),
        )


@pytest.mark.asyncio
async def test_recent_turns_maps_rows_both_jsonb_branches() -> None:
    """recent_turns мапит строки в Turn; _as_list тянет str и уже-list jsonb."""
    db = AsyncMock()
    uid = uuid4()
    created = datetime(2026, 5, 2, 10, 0, 0, tzinfo=UTC)
    db.fetch.return_value = [
        {
            "id": 2,
            "user_id": uid,
            "request_id": "req-2",
            # str-ветка (asyncpg отдаёт jsonb строкой)
            "user_message": orjson.dumps(
                [{"type": "human", "data": {"content": "q"}}]
            ).decode(),
            # list-ветка (уже распарсено)
            "react_loop": [],
            "ai_message": [{"type": "ai", "data": {"content": "a"}}],
            "token_count_full": 10,
            "token_count_loop": 0,
            "created_at": created,
        },
    ]
    repo = TurnRepository(db)

    turns = await repo.recent_turns(uid, 50)

    assert len(turns) == 1
    turn = turns[0]
    assert turn.id == 2
    assert turn.user_message == [{"type": "human", "data": {"content": "q"}}]
    assert turn.react_loop == []
    fargs = db.fetch.call_args.args
    assert "ORDER BY id DESC" in fargs[0]
    assert fargs[2] == 50


@pytest.mark.asyncio
async def test_turns_after_chronological_with_limit() -> None:
    """turns_after: id > after, хронологический порядок, limit пробрасывается."""
    db = AsyncMock()
    db.fetch.return_value = []
    repo = TurnRepository(db)
    uid = uuid4()

    await repo.turns_after(uid, 7, 30)

    sql, *args = db.fetch.call_args.args
    assert "id > $2" in sql
    assert "ORDER BY id ASC" in sql
    assert args == [uid, 7, 30]


@pytest.mark.asyncio
async def test_unprocessed_token_sum() -> None:
    """unprocessed_token_sum: COALESCE(SUM) после позиции watermark."""
    db = AsyncMock()
    db.fetch_one.return_value = {"total": 123}
    repo = TurnRepository(db)
    uid = uuid4()

    total = await repo.unprocessed_token_sum(uid, 5)

    assert total == 123
    sql, *args = db.fetch_one.call_args.args
    assert "SUM(token_count_full)" in sql
    assert args == [uid, 5]
