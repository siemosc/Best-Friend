"""MCP storage repositories: CRUD + резолв доступа (list_for_user).

Юнит на in-memory стабах (без реальной БД). SQL union/COALESCE-семантику
исполняет PostgreSQL — здесь воспроизводим её в стабе для проверки маппинга
резолв-строки в ResolvedMcpServer; реальный SQL проверяется смоуком миграции.
"""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import pytest

from bestfiend.control_plane.mcp.errors import (
    McpConnectionConflictError,
    McpConnectionNotFoundError,
    McpStorageUnavailableError,
    McpSubscriptionConflictError,
    McpSubscriptionNotFoundError,
)
from bestfiend.control_plane.mcp.repository import (
    McpConnectionRepository,
    McpSubscriptionRepository,
    _parse_tools,
    _row_to_resolved,
)


_NOW = datetime.now(UTC)


def _connection_row(
    connection_id: UUID,
    *,
    name: str = "srv",
    url: str = "https://example.com/mcp",
    transport: str = "http_stream",
    auth_type: str = "none",
    is_public: bool = False,
    is_system: bool = False,
    timeout_s: float = 30.0,
    supports_parallel_tool_calls: bool = True,
) -> dict[str, Any]:
    return {
        "connection_id": connection_id,
        "name": name,
        "url": url,
        "transport": transport,
        "auth_type": auth_type,
        "is_public": is_public,
        "is_system": is_system,
        "timeout_s": timeout_s,
        "supports_parallel_tool_calls": supports_parallel_tool_calls,
        "created_at": _NOW,
        "updated_at": None,
    }


def _subscription_row(
    user_id: UUID,
    connection_id: UUID,
    *,
    auth_token: str | None = None,
    enabled: bool = True,
    disabled_tools: str = "[]",
    timeout_s: float | None = None,
) -> dict[str, Any]:
    return {
        "user_id": user_id,
        "connection_id": connection_id,
        "auth_token": auth_token,
        "enabled": enabled,
        "disabled_tools": disabled_tools,
        "timeout_s": timeout_s,
        "created_at": _NOW,
    }


class _FetchOneStub:
    """fetch_one → заданная строка (или None), либо бросает заданное исключение."""

    def __init__(self, row: Any = None, *, raises: Exception | None = None) -> None:
        self._row = row
        self._raises = raises

    async def fetch_one(self, query: str, *args: object) -> Any:
        if self._raises is not None:
            raise self._raises
        return self._row


class _FetchManyStub:
    """fetch → заданный список строк, либо бросает."""

    def __init__(self, rows: list[Any], *, raises: Exception | None = None) -> None:
        self._rows = rows
        self._raises = raises

    async def fetch(self, query: str, *args: object) -> list[Any]:
        if self._raises is not None:
            raise self._raises
        return self._rows


# ─────────── McpConnectionRepository ───────────


@pytest.mark.asyncio
async def test_create_returns_record_with_defaults() -> None:
    conn_id = uuid4()
    db = _FetchOneStub(_connection_row(conn_id, name="websearch"))
    repo = McpConnectionRepository(db)  # type: ignore[arg-type]
    rec = await repo.create(name="websearch", url="https://x/mcp")
    assert rec.connection_id == conn_id
    assert rec.transport == "http_stream"
    assert rec.auth_type == "none"
    assert rec.timeout_s == 30.0


@pytest.mark.asyncio
async def test_create_unique_violation_maps_to_conflict() -> None:
    db = _FetchOneStub(raises=asyncpg.UniqueViolationError("dup"))
    repo = McpConnectionRepository(db)  # type: ignore[arg-type]
    with pytest.raises(McpConnectionConflictError):
        await repo.create(name="dup", url="https://x/mcp")


@pytest.mark.asyncio
async def test_get_by_id_hit() -> None:
    conn_id = uuid4()
    db = _FetchOneStub(_connection_row(conn_id))
    repo = McpConnectionRepository(db)  # type: ignore[arg-type]
    rec = await repo.get_by_id(conn_id)
    assert rec.connection_id == conn_id


@pytest.mark.asyncio
async def test_get_by_id_missing_raises_not_found() -> None:
    db = _FetchOneStub(None)
    repo = McpConnectionRepository(db)  # type: ignore[arg-type]
    with pytest.raises(McpConnectionNotFoundError):
        await repo.get_by_id(uuid4())


@pytest.mark.asyncio
async def test_get_by_id_postgres_error_maps_to_unavailable() -> None:
    db = _FetchOneStub(raises=asyncpg.PostgresError("down"))
    repo = McpConnectionRepository(db)  # type: ignore[arg-type]
    with pytest.raises(McpStorageUnavailableError):
        await repo.get_by_id(uuid4())


@pytest.mark.asyncio
async def test_update_unknown_field_rejected() -> None:
    db = _FetchOneStub(_connection_row(uuid4()))
    repo = McpConnectionRepository(db)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="Unknown field"):
        await repo.update(uuid4(), weird="x")


@pytest.mark.asyncio
async def test_delete_missing_raises_not_found() -> None:
    db = _FetchOneStub(None)
    repo = McpConnectionRepository(db)  # type: ignore[arg-type]
    with pytest.raises(McpConnectionNotFoundError):
        await repo.delete(uuid4())


# ─────────── McpSubscriptionRepository ───────────


@pytest.mark.asyncio
async def test_upsert_returns_record_with_parsed_denylist() -> None:
    user_id, conn_id = uuid4(), uuid4()
    db = _FetchOneStub(
        _subscription_row(user_id, conn_id, auth_token="t", disabled_tools='["a"]')
    )
    repo = McpSubscriptionRepository(db)  # type: ignore[arg-type]
    rec = await repo.upsert(user_id, conn_id, auth_token="t", disabled_tools=["a"])
    assert rec.auth_token == "t"
    assert rec.disabled_tools == ["a"]


@pytest.mark.asyncio
async def test_upsert_fk_violation_maps_to_conflict() -> None:
    db = _FetchOneStub(raises=asyncpg.ForeignKeyViolationError("fk"))
    repo = McpSubscriptionRepository(db)  # type: ignore[arg-type]
    with pytest.raises(McpSubscriptionConflictError):
        await repo.upsert(uuid4(), uuid4())


@pytest.mark.asyncio
async def test_get_subscription_none() -> None:
    db = _FetchOneStub(None)
    repo = McpSubscriptionRepository(db)  # type: ignore[arg-type]
    assert await repo.get(uuid4(), uuid4()) is None


@pytest.mark.asyncio
async def test_delete_subscription_missing_raises() -> None:
    db = _FetchOneStub(None)
    repo = McpSubscriptionRepository(db)  # type: ignore[arg-type]
    with pytest.raises(McpSubscriptionNotFoundError):
        await repo.delete(uuid4(), uuid4())


# ─────────── Резолв list_for_user ───────────


class _ResolveDBStub:
    """Эмуляция SQL-резолва (public ∪ private-с-подпиской, enabled-фильтр).

    Реальные union/COALESCE исполняет PostgreSQL; здесь воспроизводим семантику
    для проверки маппинга резолв-строки в ResolvedMcpServer.
    """

    def __init__(
        self,
        connections: list[dict[str, Any]],
        subscriptions: dict[tuple[UUID, UUID], dict[str, Any]],
    ) -> None:
        self._connections = connections
        self._subscriptions = subscriptions

    async def fetch(self, query: str, *args: object) -> list[Any]:
        user_id = args[0]
        assert isinstance(user_id, UUID)
        out: list[dict[str, Any]] = []
        for conn in self._connections:
            sub = self._subscriptions.get((user_id, conn["connection_id"]))
            if not conn["is_public"] and sub is None:
                continue  # private без подписки — нет доступа
            if sub is not None and not sub["enabled"]:
                continue  # подпиской выключен «для себя»
            override = sub.get("timeout_s") if sub else None
            out.append(
                {
                    "connection_id": conn["connection_id"],
                    "name": conn["name"],
                    "url": conn["url"],
                    "transport": conn["transport"],
                    "auth_type": conn["auth_type"],
                    # COALESCE(s.timeout_s, c.timeout_s)
                    "timeout_s": override
                    if override is not None
                    else conn["timeout_s"],
                    "is_public": conn["is_public"],
                    "auth_token": sub["auth_token"] if sub else None,
                    "disabled_tools": sub["disabled_tools"] if sub else "[]",
                    "supports_parallel_tool_calls": conn[
                        "supports_parallel_tool_calls"
                    ],
                }
            )
        return out


@pytest.mark.asyncio
async def test_resolve_private_with_subscription_included_with_token() -> None:
    user_id = uuid4()
    priv = _connection_row(uuid4(), name="priv", auth_type="bearer")
    subs = {
        (user_id, priv["connection_id"]): _subscription_row(
            user_id, priv["connection_id"], auth_token="secret"
        )
    }
    repo = McpSubscriptionRepository(_ResolveDBStub([priv], subs))  # type: ignore[arg-type]
    result = await repo.list_for_user(user_id)
    assert len(result) == 1
    assert result[0].auth_token == "secret"


@pytest.mark.asyncio
async def test_resolve_private_without_subscription_excluded() -> None:
    user_id = uuid4()
    priv = _connection_row(uuid4(), is_public=False)
    repo = McpSubscriptionRepository(_ResolveDBStub([priv], {}))  # type: ignore[arg-type]
    result = await repo.list_for_user(user_id)
    assert result == []


@pytest.mark.asyncio
async def test_resolve_public_without_subscription_included_no_token() -> None:
    user_id = uuid4()
    pub = _connection_row(uuid4(), is_public=True)
    repo = McpSubscriptionRepository(_ResolveDBStub([pub], {}))  # type: ignore[arg-type]
    result = await repo.list_for_user(user_id)
    assert len(result) == 1
    assert result[0].auth_token is None
    assert result[0].disabled_tools == []


@pytest.mark.asyncio
async def test_resolve_public_disabled_by_subscription_excluded() -> None:
    user_id = uuid4()
    pub = _connection_row(uuid4(), is_public=True)
    subs = {
        (user_id, pub["connection_id"]): _subscription_row(
            user_id, pub["connection_id"], enabled=False
        )
    }
    repo = McpSubscriptionRepository(_ResolveDBStub([pub], subs))  # type: ignore[arg-type]
    result = await repo.list_for_user(user_id)
    assert result == []


@pytest.mark.asyncio
async def test_resolve_public_denylist_passed_through() -> None:
    user_id = uuid4()
    pub = _connection_row(uuid4(), is_public=True)
    subs = {
        (user_id, pub["connection_id"]): _subscription_row(
            user_id, pub["connection_id"], disabled_tools='["x", "y"]'
        )
    }
    repo = McpSubscriptionRepository(_ResolveDBStub([pub], subs))  # type: ignore[arg-type]
    result = await repo.list_for_user(user_id)
    assert result[0].disabled_tools == ["x", "y"]


# ─────────── timeout override (per-user) ───────────


@pytest.mark.asyncio
async def test_upsert_persists_timeout_override() -> None:
    user_id, conn_id = uuid4(), uuid4()
    db = _FetchOneStub(_subscription_row(user_id, conn_id, timeout_s=15.0))
    repo = McpSubscriptionRepository(db)  # type: ignore[arg-type]
    rec = await repo.upsert(user_id, conn_id, timeout_s=15.0)
    assert rec.timeout_s == 15.0


@pytest.mark.asyncio
async def test_upsert_without_timeout_is_none() -> None:
    user_id, conn_id = uuid4(), uuid4()
    db = _FetchOneStub(_subscription_row(user_id, conn_id))
    repo = McpSubscriptionRepository(db)  # type: ignore[arg-type]
    rec = await repo.upsert(user_id, conn_id)
    assert rec.timeout_s is None


@pytest.mark.asyncio
async def test_resolve_timeout_override_wins_over_default() -> None:
    user_id = uuid4()
    pub = _connection_row(uuid4(), is_public=True, timeout_s=30.0)
    subs = {
        (user_id, pub["connection_id"]): _subscription_row(
            user_id, pub["connection_id"], timeout_s=15.0
        )
    }
    repo = McpSubscriptionRepository(_ResolveDBStub([pub], subs))  # type: ignore[arg-type]
    result = await repo.list_for_user(user_id)
    assert result[0].timeout_s == 15.0


@pytest.mark.asyncio
async def test_resolve_timeout_falls_back_to_connection_default() -> None:
    user_id = uuid4()
    pub = _connection_row(uuid4(), is_public=True, timeout_s=42.0)
    subs = {
        (user_id, pub["connection_id"]): _subscription_row(
            user_id, pub["connection_id"], timeout_s=None
        )
    }
    repo = McpSubscriptionRepository(_ResolveDBStub([pub], subs))  # type: ignore[arg-type]
    result = await repo.list_for_user(user_id)
    assert result[0].timeout_s == 42.0


# ─────────── list_visible_for_user (UI-вью) ───────────


class _VisibleDBStub:
    """Эмуляция list_visible_for_user: public ∪ subscribed, БЕЗ enabled-фильтра,
    дефолты сервера и оверрайды подписки — раздельно."""

    def __init__(
        self,
        connections: list[dict[str, Any]],
        subscriptions: dict[tuple[UUID, UUID], dict[str, Any]],
    ) -> None:
        self._connections = connections
        self._subscriptions = subscriptions

    async def fetch(self, query: str, *args: object) -> list[Any]:
        user_id = args[0]
        assert isinstance(user_id, UUID)
        out: list[dict[str, Any]] = []
        for conn in self._connections:
            sub = self._subscriptions.get((user_id, conn["connection_id"]))
            if not conn["is_public"] and sub is None:
                continue  # private без подписки — невидим
            has_sub = sub is not None
            out.append(
                {
                    "connection_id": conn["connection_id"],
                    "name": conn["name"],
                    "url": conn["url"],
                    "transport": conn["transport"],
                    "auth_type": conn["auth_type"],
                    "is_public": conn["is_public"],
                    "is_system": conn["is_system"],
                    "timeout_s": conn["timeout_s"],
                    "has_subscription": has_sub,
                    "sub_enabled": sub["enabled"] if sub else None,
                    "sub_auth_token": sub["auth_token"] if sub else None,
                    "sub_disabled_tools": sub["disabled_tools"] if sub else None,
                    "sub_timeout_s": sub["timeout_s"] if sub else None,
                    "sub_created_at": sub["created_at"] if sub else None,
                }
            )
        return out


@pytest.mark.asyncio
async def test_visible_public_without_subscription() -> None:
    user_id = uuid4()
    pub = _connection_row(uuid4(), is_public=True, timeout_s=30.0)
    repo = McpSubscriptionRepository(_VisibleDBStub([pub], {}))  # type: ignore[arg-type]
    result = await repo.list_visible_for_user(user_id)
    assert len(result) == 1
    assert result[0].has_subscription is False
    assert result[0].sub_timeout_s is None
    assert result[0].timeout_s == 30.0


@pytest.mark.asyncio
async def test_visible_private_with_subscription() -> None:
    user_id = uuid4()
    priv = _connection_row(uuid4(), is_public=False, auth_type="bearer")
    subs = {
        (user_id, priv["connection_id"]): _subscription_row(
            user_id, priv["connection_id"], auth_token="secret", timeout_s=20.0
        )
    }
    repo = McpSubscriptionRepository(_VisibleDBStub([priv], subs))  # type: ignore[arg-type]
    result = await repo.list_visible_for_user(user_id)
    assert len(result) == 1
    assert result[0].has_subscription is True
    assert result[0].sub_auth_token == "secret"
    assert result[0].sub_timeout_s == 20.0


@pytest.mark.asyncio
async def test_visible_none_auth_override_kept_despite_null_token() -> None:
    # Ключевой кейс: подписка с token=NULL (none-auth), но с timeout-оверрайдом —
    # ОБЯЗАНА быть видна (маркер has_subscription по строке, не по токену).
    user_id = uuid4()
    pub = _connection_row(uuid4(), is_public=True)
    subs = {
        (user_id, pub["connection_id"]): _subscription_row(
            user_id, pub["connection_id"], auth_token=None, timeout_s=12.0
        )
    }
    repo = McpSubscriptionRepository(_VisibleDBStub([pub], subs))  # type: ignore[arg-type]
    result = await repo.list_visible_for_user(user_id)
    assert result[0].has_subscription is True
    assert result[0].sub_auth_token is None
    assert result[0].sub_timeout_s == 12.0


@pytest.mark.asyncio
async def test_visible_includes_disabled_subscription() -> None:
    # Выключенная подписка всё равно в списке — иначе её нельзя было бы включить.
    user_id = uuid4()
    pub = _connection_row(uuid4(), is_public=True)
    subs = {
        (user_id, pub["connection_id"]): _subscription_row(
            user_id, pub["connection_id"], enabled=False
        )
    }
    repo = McpSubscriptionRepository(_VisibleDBStub([pub], subs))  # type: ignore[arg-type]
    result = await repo.list_visible_for_user(user_id)
    assert len(result) == 1
    assert result[0].sub_enabled is False


@pytest.mark.asyncio
async def test_create_persists_parallel_flag() -> None:
    conn_id = uuid4()
    db = _FetchOneStub(_connection_row(conn_id, supports_parallel_tool_calls=False))
    repo = McpConnectionRepository(db)  # type: ignore[arg-type]
    rec = await repo.create(
        name="seq", url="https://x/mcp", supports_parallel_tool_calls=False
    )
    assert rec.supports_parallel_tool_calls is False


@pytest.mark.asyncio
async def test_resolve_carries_parallel_flag() -> None:
    user_id = uuid4()
    pub = _connection_row(uuid4(), is_public=True, supports_parallel_tool_calls=False)
    repo = McpSubscriptionRepository(_ResolveDBStub([pub], {}))  # type: ignore[arg-type]
    result = await repo.list_for_user(user_id)
    assert result[0].supports_parallel_tool_calls is False


def test_parse_tools_handles_str_list_and_none() -> None:
    assert _parse_tools('["a", "b"]') == ["a", "b"]
    assert _parse_tools(["a", "b"]) == ["a", "b"]
    assert _parse_tools(None) == []


# ─────────── none ⇒ без токена в резолве ───────────


def _resolved_row(auth_type: str, auth_token: str | None) -> dict[str, Any]:
    return {
        "connection_id": uuid4(),
        "name": "srv",
        "url": "https://example.com/mcp",
        "transport": "http_stream",
        "auth_type": auth_type,
        "timeout_s": 30.0,
        "is_public": True,
        "auth_token": auth_token,
        "disabled_tools": "[]",
        "supports_parallel_tool_calls": True,
    }


def test_row_to_resolved_none_auth_drops_stale_token() -> None:
    """Легаси auth_token подписки при auth_type=none не попадает в резолв."""
    resolved = _row_to_resolved(_resolved_row("none", "stale-token"))
    assert resolved.auth_token is None


def test_row_to_resolved_bearer_keeps_token() -> None:
    resolved = _row_to_resolved(_resolved_row("bearer", "tok"))
    assert resolved.auth_token == "tok"
