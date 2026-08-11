"""Тесты GraphRuntime: сборка GraphContext/State, бюджет, MCP-discovery, e2e-оркестрация."""

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from langchain_core.messages import AIMessage, HumanMessage
import pytest

from bestfiend.contracts.artifacts import ArtifactRef
from bestfiend.contracts.events import AnswerFinal, InputEvent, ProgressStep
from bestfiend.contracts.mcp import ResolvedMcpServer
from bestfiend.control_plane.mcp import McpStorageError
from bestfiend.control_plane.model_registry.contracts import ResolveModelResponse
from bestfiend.graph.config import GraphSettings, ModelIDSettings
from bestfiend.graph.nodes.error.messages import STATIC_TEXTS
from bestfiend.graph.runtime import GraphRuntime, _build_request_meta
from bestfiend.mcp.contracts import DiscoveryFailure, ServerDiscovery, ToolInfo
from bestfiend.memory.contracts import MemoryContext
from bestfiend.memory.settings import MemorySettings
from bestfiend.primitives.background_tasks import BackgroundTaskSupervisor
from tests.graph.fakes import StreamPublisherFake, bindable_chat_model


def _runtime(
    *,
    publisher: StreamPublisherFake | None = None,
    graph: Any = None,
    mcp_server_resolver: Any = None,
    memory_runtime: Any = None,
    model_registry: Any = None,
    handler_provider: Any = None,
) -> GraphRuntime:
    return GraphRuntime(
        stream_publisher=publisher or StreamPublisherFake(),  # type: ignore[arg-type]
        graph=graph or MagicMock(),
        settings=GraphSettings(),  # pyright: ignore[reportCallIssue]
        model_registry=model_registry or AsyncMock(),
        memory_runtime=memory_runtime or MagicMock(),
        artifacts=MagicMock(),
        background_tasks=BackgroundTaskSupervisor(),
        mcp_server_resolver=mcp_server_resolver,
        model_id_settings=ModelIDSettings(model_id="test-model"),
        langfuse_handler_provider=handler_provider,
    )


def _event() -> InputEvent:
    return InputEvent(
        user_id=uuid4(),
        message="привет",
        channel="telegram",
        request_id="req-1",
    )


def _rc(**over: Any) -> ResolveModelResponse:
    base: dict[str, Any] = {"config": {"provider": "openai", "model": "x"}}
    base.update(over)
    return ResolveModelResponse(**base)


def _memory_runtime() -> Any:
    mr = MagicMock()
    mr.stm_repository = MagicMock()
    # Реальные настройки: budget-математика требует чисел, не MagicMock.
    mr.memory_settings = MemorySettings()
    return mr


# ── _build_state: инъекция приложенных артефактов ─────────────────────


@pytest.mark.asyncio
async def test_build_state_injects_attached_artifacts(monkeypatch) -> None:
    """attached_artifacts впечатываются в текущий Human; turn_start_index — на него."""
    ref = ArtifactRef(
        artifact_id="abc123def456",
        artifact_user_name="report.csv",
        type="table",
        description="Сводка за май",
    )
    event = InputEvent(
        user_id=uuid4(),
        message="что в файле?",
        channel="telegram",
        request_id="req-1",
        attached_artifacts=[ref],
    )
    rt = _runtime()
    memory = MemoryContext(log_tail=[HumanMessage(content="что в файле?")])

    state = rt._build_state(event, _rc(), [], memory)

    human = state.stm[-1]
    assert state.turn_start_index == len(state.stm) - 1
    assert isinstance(human, HumanMessage)
    assert isinstance(human.content, str)
    assert "Приложенные файлы:" in human.content
    assert "`report_def456.csv`" in human.content
    assert human.additional_kwargs["attached_artifacts"][0]["artifact_id"] == (
        "abc123def456"
    )


@pytest.mark.asyncio
async def test_build_state_without_attachments_keeps_plain_human(monkeypatch) -> None:
    """Без приложенных файлов текущий Human не трогаем."""
    rt = _runtime()
    memory = MemoryContext(log_tail=[HumanMessage(content="привет")])

    state = rt._build_state(_event(), _rc(), [], memory)

    human = state.stm[-1]
    assert isinstance(human, HumanMessage)
    assert human.content == "привет"
    assert "attached_artifacts" not in human.additional_kwargs


# ── _build_context ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_build_context_assembles_model_tools_graph(monkeypatch) -> None:
    """Единая модель, переданные tools и self-ссылка графа собраны в context."""
    graph = MagicMock()
    rt = _runtime(graph=graph)
    monkeypatch.setattr(
        "bestfiend.graph.runtime.build_chat_model", lambda cfg: MagicMock()
    )

    ctx = await rt._build_context(
        _event(), _rc(), {"search": MagicMock()}, {}, hydrate_images=None
    )

    assert ctx is not None
    assert ctx.model is not None
    assert "search" in ctx.tools_by_name
    # Тулзы памяти добавлены и помечены top-level-only.
    assert "memory_search" in ctx.tools_by_name
    assert "memory_save" in ctx.tools_by_name
    assert "memory_revise" in ctx.tools_by_name
    assert "memory_read_log" in ctx.tools_by_name
    assert "memory_track" in ctx.tools_by_name
    assert "memory_stats" in ctx.tools_by_name
    assert ctx.top_level_only_tool_names == {
        "memory_search",
        "memory_save",
        "memory_revise",
        "memory_read_log",
        "memory_track",
        "memory_stats",
    }
    assert ctx.graph is graph


@pytest.mark.asyncio
async def test_build_context_empty_tools(monkeypatch) -> None:
    """Пустой набор тулов → context с пустым tools_by_name."""
    rt = _runtime()
    monkeypatch.setattr(
        "bestfiend.graph.runtime.build_chat_model", lambda cfg: MagicMock()
    )

    ctx = await rt._build_context(_event(), _rc(), {}, {}, hydrate_images=None)

    assert ctx is not None
    # Без MCP-тулзов остаются только тулзы памяти.
    assert set(ctx.tools_by_name) == {
        "memory_search",
        "memory_save",
        "memory_revise",
        "memory_read_log",
        "memory_track",
        "memory_stats",
    }


@pytest.mark.asyncio
async def test_build_context_model_build_fails(monkeypatch) -> None:
    """build_chat_model кидает → static AnswerFinal + close, контекст None."""
    pub = StreamPublisherFake()
    rt = _runtime(publisher=pub)

    def _boom(cfg: Any) -> Any:
        raise ValueError("bad config")

    monkeypatch.setattr("bestfiend.graph.runtime.build_chat_model", _boom)

    ctx = await rt._build_context(_event(), _rc(), {}, {}, hydrate_images=None)

    assert ctx is None
    assert len(pub.published) == 1
    assert isinstance(pub.published[0], AnswerFinal)
    assert pub.published[0].text == STATIC_TEXTS["provider_down"]
    assert pub.closed == ["req-1"]


@pytest.mark.asyncio
async def test_build_context_empty_config(monkeypatch) -> None:
    """Пустой config → static + None, build_chat_model не зван."""
    pub = StreamPublisherFake()
    rt = _runtime(publisher=pub)
    called: list[int] = []
    monkeypatch.setattr(
        "bestfiend.graph.runtime.build_chat_model",
        lambda cfg: called.append(1) or MagicMock(),
    )

    rc = _rc(config={})
    ctx = await rt._build_context(_event(), rc, {}, {}, hydrate_images=None)

    assert ctx is None
    assert called == []
    assert pub.published[0].text == STATIC_TEXTS["provider_down"]
    assert pub.closed == ["req-1"]


# ── _plan_budget ──────────────────────────────────────────────────────


def test_plan_budget_falls_back_on_malformed_window() -> None:
    """Битый context_window/max_tokens в конфиге модели → бюджет на дефолтах, не падение."""
    rt = _runtime(memory_runtime=_memory_runtime())
    good = rt._plan_budget(_rc(), _event())
    broken = rt._plan_budget(
        _rc(config={"provider": "openai", "model": "x", "context_window": "oops"}),
        _event(),
    )
    # Кривое окно не роняет запрос: fail-open к тем же дефолтам, что и без окна.
    assert broken == good


# ── e2e: process_input_event через реальный граф ──────────────────────


@pytest.mark.asyncio
async def test_process_input_event_e2e(monkeypatch) -> None:
    """Событие → реальный граф (react plain-text) → AnswerFinal + close; persist зван."""
    from bestfiend.graph.graph import build_graph

    monkeypatch.setattr(
        "bestfiend.graph.runtime.memory_search",
        AsyncMock(
            return_value=MemoryContext(log_tail=[HumanMessage(content="привет")])
        ),
    )
    monkeypatch.setattr(
        "bestfiend.graph.runtime.build_chat_model",
        lambda cfg: bindable_chat_model([AIMessage(content="ответ от ассистента")]),
    )
    write_mock = AsyncMock()
    monkeypatch.setattr("bestfiend.graph.persist.memory_write", write_mock)

    pub = StreamPublisherFake()
    rt = _runtime(
        publisher=pub,
        graph=build_graph(),
        memory_runtime=_memory_runtime(),
        model_registry=AsyncMock(resolve=AsyncMock(return_value=_rc())),
    )

    await rt.process_input_event(_event())
    await asyncio.sleep(0.05)  # дать фоновому persist-таску отработать

    finals = [e for e in pub.published if isinstance(e, AnswerFinal)]
    assert len(finals) == 1
    assert finals[0].text == "ответ от ассистента"
    assert pub.closed == ["req-1"]
    write_mock.assert_awaited_once()
    # persist пишет доставленный текст как ai_message хода.
    assert write_mock.await_args is not None
    req = write_mock.await_args.args[1]
    assert req.ai_message[0]["data"]["content"] == "ответ от ассистента"


# ── _discover_mcp / _notify_discovery_failures ────────────────────────


def _resolved(
    name: str, conn_id: Any, *, supports_parallel: bool = True
) -> ResolvedMcpServer:
    return ResolvedMcpServer(
        connection_id=conn_id,
        name=name,
        url="https://example.com/mcp",
        transport="http_stream",
        auth_type="none",
        timeout_s=30.0,
        is_public=True,
        auth_token=None,
        disabled_tools=[],
        supports_parallel_tool_calls=supports_parallel,
    )


@pytest.mark.asyncio
async def test_discover_mcp_no_repo_returns_empty() -> None:
    """Репозиторий не прокинут → пустой набор тулов (граф работает без MCP)."""
    rt = _runtime()
    tools, catalog, _serial, failures = await rt._discover_mcp(_event())
    assert tools == {}
    assert catalog == []
    assert failures == []


@pytest.mark.asyncio
async def test_discover_mcp_resolves_and_builds_tools(monkeypatch) -> None:
    """Резолв серверов → discovery → namespaced-тулы + каталог."""
    cid = uuid4()
    repo = AsyncMock()
    repo.list_for_user.return_value = [_resolved("websearch", cid)]
    discovery = ServerDiscovery(
        connection_id=cid,
        name="websearch",
        instructions="instr",
        tools=[
            ToolInfo(name="search", description="d", input_schema={"type": "object"})
        ],
        failure=None,
    )
    monkeypatch.setattr(
        "bestfiend.graph.runtime.discover_servers",
        AsyncMock(return_value=[discovery]),
    )
    rt = _runtime(mcp_server_resolver=repo)

    tools, catalog, _serial, failures = await rt._discover_mcp(_event())

    assert "websearch__search" in tools
    assert catalog[0].name == "websearch"
    assert failures == []


@pytest.mark.asyncio
async def test_discover_mcp_storage_error_graceful() -> None:
    """Сбой storage при резолве → graceful пусто, граф продолжает без тулов."""
    repo = AsyncMock()
    repo.list_for_user.side_effect = McpStorageError("db down")
    rt = _runtime(mcp_server_resolver=repo)

    tools, catalog, _serial, failures = await rt._discover_mcp(_event())

    assert tools == {}
    assert catalog == []
    assert failures == []


@pytest.mark.asyncio
async def test_discover_mcp_collects_failures(monkeypatch) -> None:
    """Недоступный сервер → его failure собран (тулов нет)."""
    cid = uuid4()
    repo = AsyncMock()
    repo.list_for_user.return_value = [_resolved("broken", cid)]
    discovery = ServerDiscovery(
        connection_id=cid,
        name="broken",
        instructions=None,
        tools=[],
        failure=DiscoveryFailure(kind="unreachable", message="refused"),
    )
    monkeypatch.setattr(
        "bestfiend.graph.runtime.discover_servers",
        AsyncMock(return_value=[discovery]),
    )
    rt = _runtime(mcp_server_resolver=repo)

    tools, _, _serial, failures = await rt._discover_mcp(_event())

    assert tools == {}
    assert failures == [("broken", discovery.failure)]


@pytest.mark.asyncio
async def test_discover_mcp_serial_map_for_non_parallel_server(monkeypatch) -> None:
    """Сервер supports_parallel_tool_calls=false → его тулзы попадают в serial-map."""
    cid = uuid4()
    repo = AsyncMock()
    repo.list_for_user.return_value = [_resolved("seq", cid, supports_parallel=False)]
    discovery = ServerDiscovery(
        connection_id=cid,
        name="seq",
        instructions="instr",
        tools=[ToolInfo(name="run", description="d", input_schema={"type": "object"})],
        failure=None,
    )
    monkeypatch.setattr(
        "bestfiend.graph.runtime.discover_servers",
        AsyncMock(return_value=[discovery]),
    )
    rt = _runtime(mcp_server_resolver=repo)

    tools, _catalog, serial, _failures = await rt._discover_mcp(_event())

    assert "seq__run" in tools
    assert serial == {"seq__run": str(cid)}


def test_build_request_meta_carries_user_id() -> None:
    """`_build_request_meta` кладёт str(user_id) под плоский ключ user_id."""
    event = _event()
    assert _build_request_meta(event) == {"user_id": str(event.user_id)}


@pytest.mark.asyncio
async def test_notify_failures_publishes_progress_step() -> None:
    """Каждый фейл discovery → ProgressStep юзеру (тот же путь, что прогресс-шаги)."""
    pub = StreamPublisherFake()
    rt = _runtime(publisher=pub)
    failure = DiscoveryFailure(kind="timeout", message="slow")

    await rt._notify_discovery_failures("req-1", [("slowserver", failure)])

    assert len(pub.published) == 1
    assert isinstance(pub.published[0], ProgressStep)
    assert "slowserver" in pub.published[0].text
