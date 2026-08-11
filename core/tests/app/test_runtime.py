"""Тесты транзакционного lifecycle CoreRuntime."""

from types import SimpleNamespace
from typing import Any, cast

import pytest

import bestfiend.app.runtime as runtime_module
from bestfiend.app.runtime import CoreRuntime


def _runtime(
    events: list[str],
    *,
    artifacts_start_error: Exception | None = None,
) -> CoreRuntime:
    """Собрать CoreRuntime на наблюдаемых тестовых ресурсах."""

    async def db_connect() -> None:
        """Записать подключение основной БД."""
        events.append("db.connect")

    async def db_disconnect() -> None:
        """Записать отключение основной БД."""
        events.append("db.disconnect")

    async def memory_start() -> None:
        """Записать запуск памяти."""
        events.append("memory.start")

    async def memory_stop() -> None:
        """Записать остановку памяти."""
        events.append("memory.stop")

    async def memory_stop_scheduling() -> None:
        """Записать остановку sleep-таймеров."""
        events.append("memory.stop_scheduling")

    async def artifacts_start() -> None:
        """Записать запуск артефактов или имитировать сбой."""
        events.append("artifacts.start")
        if artifacts_start_error is not None:
            raise artifacts_start_error

    async def artifacts_stop() -> None:
        """Записать остановку артефактов."""
        events.append("artifacts.stop")

    async def dashboard_close() -> None:
        """Записать закрытие dashboard-клиента."""
        events.append("dashboard.close")

    async def background_shutdown(*, timeout_s: float) -> None:
        """Записать остановку фоновых задач."""
        events.append(f"background.shutdown:{timeout_s:g}")

    db = SimpleNamespace(connect=db_connect, disconnect=db_disconnect)
    memory = SimpleNamespace(
        start=memory_start,
        stop=memory_stop,
        stop_scheduling=memory_stop_scheduling,
        memory_settings=SimpleNamespace(),
    )
    artifacts = SimpleNamespace(
        start=artifacts_start,
        stop=artifacts_stop,
        service=SimpleNamespace(),
    )
    dashboard = SimpleNamespace(aclose=dashboard_close)
    background_tasks = SimpleNamespace(shutdown=background_shutdown)
    return CoreRuntime(
        db=cast(Any, db),
        user_service=cast(Any, SimpleNamespace()),
        auth_service=cast(Any, SimpleNamespace()),
        auth_settings=cast(Any, SimpleNamespace()),
        background_tasks=cast(Any, background_tasks),
        graph_settings=cast(Any, SimpleNamespace()),
        model_id_settings=cast(Any, SimpleNamespace()),
        model_registry=cast(Any, SimpleNamespace()),
        memory_runtime=cast(Any, memory),
        artifacts_runtime=cast(Any, artifacts),
        dashboard_service=cast(Any, dashboard),
        assistant_service=cast(Any, SimpleNamespace()),
        mcp_subscription_repository=cast(Any, SimpleNamespace()),
        mcp_management_service=cast(Any, SimpleNamespace()),
        mcp_oauth_service=cast(Any, SimpleNamespace()),
        mcp_resolve_service=cast(Any, SimpleNamespace()),
        public_base_url="http://localhost:5173",
    )


@pytest.mark.asyncio
async def test_start_rolls_back_acquired_resources() -> None:
    """Сбой старта должен закрыть все уже созданные ресурсы в обратном порядке."""
    events: list[str] = []
    runtime = _runtime(events, artifacts_start_error=RuntimeError("storage down"))

    with pytest.raises(RuntimeError, match="storage down"):
        await runtime.start()

    assert events == [
        "db.connect",
        "memory.start",
        "artifacts.start",
        "artifacts.stop",
        "memory.stop",
        "db.disconnect",
        "dashboard.close",
    ]


@pytest.mark.asyncio
async def test_stop_uses_declared_shutdown_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Штатный shutdown должен сначала гасить ingress и фоновые записи."""
    events: list[str] = []
    runtime = _runtime(events)

    class FakeLangfuse:
        """Наблюдаемый клиент трассировки."""

        def __init__(self, **_kwargs: object) -> None:
            """Создать тестовый клиент без внешнего I/O."""

        def flush(self) -> None:
            """Записать flush."""
            events.append("langfuse.flush")

        def shutdown(self) -> None:
            """Записать shutdown."""
            events.append("langfuse.shutdown")

    tracing = SimpleNamespace(
        langfuse_enabled=False,
        langfuse_public_key="",
        langfuse_secret_key="",
        langfuse_base_url="http://langfuse.invalid",
        langfuse_flush_interval=1,
    )
    telegram = SimpleNamespace(telegram_bot_token="")
    monkeypatch.setattr(runtime_module, "TracingSettings", lambda: tracing)
    monkeypatch.setattr(runtime_module, "TelegramBotSettings", lambda: telegram)
    monkeypatch.setattr(runtime_module, "Langfuse", FakeLangfuse)
    monkeypatch.setattr(runtime_module, "build_graph", lambda: cast(Any, object()))

    await runtime.start()
    events.clear()
    await runtime.stop()

    assert events == [
        "dashboard.close",
        "memory.stop_scheduling",
        "background.shutdown:10",
        "memory.stop",
        "artifacts.stop",
        "db.disconnect",
        "langfuse.flush",
        "langfuse.shutdown",
    ]
    assert runtime.graph_runtime is None
    assert runtime.stream_publisher is None
