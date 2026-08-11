"""Композиционный корень core: CoreRuntime + сборка зависимостей всех граней.

Control-plane (users/auth) + graph stack (memory_runtime, artifacts_runtime,
model_registry, graph_runtime, stream_publisher). Graph-поля опциональны для
тестов; production-bootstrap (`build_runtime` + `start()`) заполняет всё.

Sequence start:
    DB → memory → artifacts → Langfuse → compile graph → GraphRuntime.
Stop — reverse: на закрытии flush + shutdown Langfuse-клиента.
"""

from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Any

from langfuse import Langfuse, get_client, propagate_attributes
from langfuse.langchain import CallbackHandler
from loguru import logger

from bestfiend.ai.stt import OpenAICompatibleSpeechTranscriber, SttSettings
from bestfiend.app.db import CorePostgreSQLClient
from bestfiend.app.settings import (
    CoreDatabaseSettings,
    PublicUrlSettings,
    TracingSettings,
)
from bestfiend.app.stream_publisher import StreamPublisher
from bestfiend.artifacts.runtime import ArtifactsRuntime
from bestfiend.artifacts.runtime import (
    create_artifacts_runtime as build_artifacts_runtime,
)
from bestfiend.contracts.events import InputEvent
from bestfiend.control_plane.assistant.repository import UserAssistantConfigRepository
from bestfiend.control_plane.assistant.service import UserAssistantConfigService
from bestfiend.control_plane.auth.repository import (
    AuthUserRepository,
    BindingCodeRepository,
    SessionRepository,
)
from bestfiend.control_plane.auth.service import AuthService
from bestfiend.control_plane.dashboard import (
    DashboardService,
    DashboardSettings,
    HealthProbeClient,
)
from bestfiend.control_plane.mcp import (
    McpConnectionRepository,
    McpSubscriptionRepository,
)
from bestfiend.control_plane.mcp.oauth.repository import (
    McpOAuthClientRepository,
    McpOAuthFlowRepository,
    McpOAuthTokenRepository,
)
from bestfiend.control_plane.mcp.oauth.service import McpOAuthService
from bestfiend.control_plane.mcp.oauth.token_client import OAuthTokenClient
from bestfiend.control_plane.mcp.resolve import McpResolveService
from bestfiend.control_plane.mcp.service import McpManagementService
from bestfiend.control_plane.model_registry import ModelRegistry
from bestfiend.control_plane.model_registry.errors import ModelNotFoundError
from bestfiend.control_plane.model_registry.repository import ModelConfigRepository
from bestfiend.control_plane.settings import AuthSettings
from bestfiend.control_plane.users.repository import UserRepository
from bestfiend.control_plane.users.service import UserService
from bestfiend.graph.config import GraphSettings, ModelIDSettings
from bestfiend.graph.graph import build_graph
from bestfiend.graph.runtime import GraphRuntime
from bestfiend.mcp.settings import McpDiscoverySettings
from bestfiend.memory.runtime import MemoryRuntime
from bestfiend.memory.runtime import create_memory_runtime as build_memory_runtime
from bestfiend.primitives.background_tasks import BackgroundTaskSupervisor
from bestfiend.telegram import (
    TelegramBot,
    TelegramBotSettings,
    TelegramRuntime,
    parse_allowed_user_ids,
)
from bestfiend.telegram.request_correlation import RequestCorrelation


@dataclass(slots=True)
class CoreRuntime:
    """Полный runtime монолита. Живёт в `app.state.runtime`.

    Все зависимости композиции обязательны. Изменяемые lifecycle-handle поля
    заполняются только после успешного запуска соответствующего ресурса.
    """

    db: CorePostgreSQLClient
    user_service: UserService
    auth_service: AuthService
    auth_settings: AuthSettings
    background_tasks: BackgroundTaskSupervisor
    graph_settings: GraphSettings
    model_id_settings: ModelIDSettings
    model_registry: ModelRegistry
    memory_runtime: MemoryRuntime
    artifacts_runtime: ArtifactsRuntime
    dashboard_service: DashboardService
    assistant_service: UserAssistantConfigService
    mcp_subscription_repository: McpSubscriptionRepository
    mcp_management_service: McpManagementService
    mcp_oauth_service: McpOAuthService
    mcp_resolve_service: McpResolveService
    public_base_url: str
    # ----- Lifecycle handles -----
    stream_publisher: StreamPublisher | None = None
    graph_runtime: GraphRuntime | None = None
    langfuse_client: Langfuse | None = None
    telegram_runtime: TelegramRuntime | None = None
    _started: bool = field(default=False, init=False)
    _stopped: bool = field(default=False, init=False)
    _shutdown_stack: AsyncExitStack | None = field(default=None, init=False)

    async def start(self) -> None:
        """Запускает все слои в правильном порядке (control-plane → memory → ... → graph)."""
        if self._started:
            return
        if self._stopped:
            raise RuntimeError("остановленный CoreRuntime нельзя запустить повторно")

        rollback = AsyncExitStack()
        rollback.push_async_callback(self.dashboard_service.aclose)
        rollback.push_async_callback(self.db.disconnect)
        rollback.push_async_callback(self.memory_runtime.stop)
        rollback.push_async_callback(self.artifacts_runtime.stop)
        try:
            # DB pool первым: core — schema-owner, миграции прогоняются при connect.
            await self.db.connect()
            await self.memory_runtime.start()
            await self.artifacts_runtime.start()
            # Graph stack: Langfuse → StreamPublisher → compile graph → GraphRuntime → Telegram.
            await self._start_graph_stack(rollback)
        except BaseException:
            try:
                await rollback.aclose()
            except Exception:
                logger.exception("CoreRuntime: ошибка отката после неудачного запуска")
            self._clear_started_handles()
            self._stopped = True
            raise

        rollback.pop_all()
        self._shutdown_stack = self._build_shutdown_stack()

        self._started = True
        logger.info("CoreRuntime: started")

    async def _start_graph_stack(self, rollback: AsyncExitStack) -> None:
        """Поднимает граф-стек: Langfuse + StreamPublisher + граф + GraphRuntime + Telegram.

        MCP-discovery — пер-реквест в GraphRuntime (без startup-registry, CF снесён).
        Telegram стартует только при наличии токена (graceful skip).
        """
        tracing = TracingSettings()  # pyright: ignore[reportCallIssue]
        active = (
            tracing.langfuse_enabled
            and bool(tracing.langfuse_public_key)
            and bool(tracing.langfuse_secret_key)
        )
        try:
            self.langfuse_client = Langfuse(
                public_key=tracing.langfuse_public_key,
                secret_key=tracing.langfuse_secret_key,
                base_url=tracing.langfuse_base_url,
                flush_interval=tracing.langfuse_flush_interval,
                tracing_enabled=active,
            )
        except Exception as exc:
            logger.error("Ошибка инициализации Langfuse: {}", exc)
            self.langfuse_client = None
        if self.langfuse_client is not None:
            rollback.callback(self._shutdown_langfuse)
        logger.info("Langfuse трейсинг {}", "включён" if active else "отключён")

        self.stream_publisher = StreamPublisher()
        self.graph_runtime = GraphRuntime(
            stream_publisher=self.stream_publisher,
            graph=build_graph(),
            settings=self.graph_settings,
            model_registry=self.model_registry,
            memory_runtime=self.memory_runtime,
            artifacts=self.artifacts_runtime.service,
            background_tasks=self.background_tasks,
            mcp_server_resolver=self.mcp_resolve_service,
            mcp_discovery_settings=McpDiscoverySettings(),  # pyright: ignore[reportCallIssue]
            model_id_settings=self.model_id_settings,
            langfuse_handler_provider=(lambda: CallbackHandler()) if active else None,
        )
        await self._start_telegram_bot()
        if self.telegram_runtime is not None:
            rollback.push_async_callback(self.telegram_runtime.stop)

    async def _start_telegram_bot(self) -> None:
        """Стартует Telegram-бота при наличии токена; иначе graceful skip."""
        settings = TelegramBotSettings()  # pyright: ignore[reportCallIssue]
        if not settings.telegram_bot_token:
            logger.info("CoreRuntime: TELEGRAM_BOT_TOKEN не задан, бот пропущен")
            return
        stream_publisher = self.stream_publisher
        if stream_publisher is None:
            raise RuntimeError("stream publisher не инициализирован до старта Telegram")
        # STT: пустой STT_URL — фича выключена, бот отвечает отказом на голосовые.
        stt_settings = SttSettings()  # pyright: ignore[reportCallIssue]
        transcriber = (
            OpenAICompatibleSpeechTranscriber(
                base_url=stt_settings.stt_url,
                model=stt_settings.stt_model,
                timeout_s=stt_settings.stt_timeout_s,
            )
            if stt_settings.stt_url
            else None
        )
        if transcriber is None:
            logger.info("CoreRuntime: STT_URL не задан, транскрипция выключена")
        bot = TelegramBot(
            bot_token=settings.telegram_bot_token,
            user_service=self.user_service,
            publish_input_event=self.publish_input_event,
            artifacts=self.artifacts_runtime.service,
            outbound_source=stream_publisher,
            attachment_max_size_bytes=settings.attachment_max_size_bytes,
            allowed_user_ids=parse_allowed_user_ids(settings.telegram_allowed_user_ids),
            auth_service=self.auth_service,
            binding_code_ttl_s=self.auth_settings.binding_code_ttl_s,
            inbox_debounce_s=settings.telegram_inbox_debounce_s,
            transcriber=transcriber,
            stt_max_duration_s=stt_settings.stt_max_duration_s,
        )
        self.telegram_runtime = TelegramRuntime(bot=bot)
        await self.telegram_runtime.start()

    async def stop(self) -> None:
        """Остановить ресурсы в порядке, зафиксированном при успешном старте."""
        if not self._started:
            return
        logger.info("CoreRuntime: stopping")
        shutdown_stack = self._shutdown_stack
        try:
            if shutdown_stack is not None:
                await shutdown_stack.aclose()
        finally:
            self._shutdown_stack = None
            self._started = False
            self._stopped = True
            self._clear_started_handles()
        logger.info("CoreRuntime: stopped")

    def _build_shutdown_stack(self) -> AsyncExitStack:
        """Зафиксировать единый порядок штатного shutdown."""
        stack = AsyncExitStack()
        stack.callback(self._shutdown_langfuse)
        stack.push_async_callback(self.db.disconnect)
        stack.push_async_callback(self.artifacts_runtime.stop)
        stack.push_async_callback(self.memory_runtime.stop)
        stack.push_async_callback(self.background_tasks.shutdown, timeout_s=10)
        stack.push_async_callback(self.memory_runtime.stop_scheduling)
        stack.push_async_callback(self.dashboard_service.aclose)
        if self.telegram_runtime is not None:
            stack.push_async_callback(self.telegram_runtime.stop)
        return stack

    def _shutdown_langfuse(self) -> None:
        """Сбросить буфер и закрыть клиент трассировки без срыва shutdown."""
        client = self.langfuse_client
        self.langfuse_client = None
        if client is None:
            return
        try:
            client.flush()
            client.shutdown()
        except Exception as exc:
            logger.warning("Ошибка shutdown Langfuse: {}", exc)

    def _clear_started_handles(self) -> None:
        """Очистить handles, которые действительны только во время работы."""
        self.telegram_runtime = None
        self.graph_runtime = None
        self.stream_publisher = None
        self.langfuse_client = None

    async def publish_input_event(
        self,
        *,
        event: InputEvent,
        request_correlation: RequestCorrelation,
    ) -> None:
        """Запускает обработку InputEvent внутри собственного root trace.

        Subscription (`stream_publisher.open(request_id)`) обязан быть открыт
        caller'ом (TelegramBot) ДО вызова — иначе первый `publish()` уйдёт в drop.
        """
        if self.graph_runtime is None:
            raise RuntimeError("CoreRuntime.publish_input_event: graph_runtime is None")
        trace_metadata = {
            "request_id": request_correlation.request_id,
            "channel": event.channel,
        }
        input_data: dict[str, Any] = {
            "processing_mode": event.processing_mode,
            "request_id": request_correlation.request_id,
            "user_id": request_correlation.user_id,
            "message": event.message,
            "channel": event.channel,
            "metadata": event.metadata,
        }
        client = get_client()
        with (
            client.start_as_current_observation(
                name="bestfiend-core.ingress",
                as_type="span",
                input=input_data,
                metadata=trace_metadata,
            ),
            propagate_attributes(
                user_id=request_correlation.user_id,
                session_id=request_correlation.request_id,
                metadata=trace_metadata,
            ),
        ):
            await self.graph_runtime.process_input_event(event)


def build_runtime() -> CoreRuntime:
    """Собирает CoreRuntime из окружения (core/.env).

    Sync-конструктор: собирает clients/services. Async-init (DB connect,
    tracing, compile graph) — в `start()`.
    """
    db = CorePostgreSQLClient(CoreDatabaseSettings())
    user_repository = UserRepository(db_client=db)
    session_repository = SessionRepository(db_client=db)
    binding_code_repository = BindingCodeRepository(db_client=db)
    auth_user_repository = AuthUserRepository(db_client=db)
    auth_settings = AuthSettings()  # pyright: ignore[reportCallIssue]

    # ----- Graph stack -----
    graph_settings = GraphSettings()  # pyright: ignore[reportCallIssue]
    model_id_settings = ModelIDSettings()  # pyright: ignore[reportCallIssue]
    user_assistant_config_repository = UserAssistantConfigRepository(db)
    model_config_repository = ModelConfigRepository(db)
    mcp_connection_repository = McpConnectionRepository(db)
    mcp_subscription_repository = McpSubscriptionRepository(db)
    mcp_discovery_settings = McpDiscoverySettings()  # pyright: ignore[reportCallIssue]

    # ----- MCP OAuth stack -----
    public_url_settings = PublicUrlSettings()  # pyright: ignore[reportCallIssue]
    public_base_url = public_url_settings.public_base_url
    mcp_oauth_client_repository = McpOAuthClientRepository(db)
    mcp_oauth_flow_repository = McpOAuthFlowRepository(db)
    mcp_oauth_token_repository = McpOAuthTokenRepository(db)
    mcp_oauth_token_client = OAuthTokenClient(
        timeout_s=mcp_discovery_settings.mcp_discovery_timeout_s,
    )
    mcp_oauth_service = McpOAuthService(
        client_repository=mcp_oauth_client_repository,
        flow_repository=mcp_oauth_flow_repository,
        token_repository=mcp_oauth_token_repository,
        connection_repository=mcp_connection_repository,
        subscription_repository=mcp_subscription_repository,
        token_client=mcp_oauth_token_client,
        redirect_uri=f"{public_base_url}/api/mcp/oauth/callback",
    )
    mcp_management_service = McpManagementService(
        connection_repository=mcp_connection_repository,
        subscription_repository=mcp_subscription_repository,
        oauth_service=mcp_oauth_service,
        discovery_settings=mcp_discovery_settings,
    )
    # Резолвер серверов для графа: подписки + живой OAuth-access (замена «репо как резолвер»).
    mcp_resolve_service = McpResolveService(
        subscription_repository=mcp_subscription_repository,
        oauth_service=mcp_oauth_service,
    )

    # ----- Identity writers -----
    assistant_service = UserAssistantConfigService(
        user_config_repository=user_assistant_config_repository,
    )
    user_service = UserService(
        repository=user_repository,
        assistant_service=assistant_service,
    )
    # AuthService с полным writer-набором (login/bind/binding-codes).
    auth_service = AuthService(
        session_repository=session_repository,
        user_service=user_service,
        binding_repository=binding_code_repository,
        auth_user_repository=auth_user_repository,
        settings=auth_settings,
    )
    model_registry = ModelRegistry(
        model_repository=model_config_repository,
        user_config_repository=user_assistant_config_repository,
        user_repository=user_repository,
    )

    async def _load_model_config(model_id: str) -> dict[str, Any] | None:
        """Конфиг модели памяти по id; отсутствие в models → None (память деградирует)."""
        try:
            record = await model_config_repository.get_by_id(model_id)
        except ModelNotFoundError:
            return None
        return dict(record.config)

    memory_runtime = build_memory_runtime(model_config_loader=_load_model_config)
    artifacts_runtime = build_artifacts_runtime()

    # ----- Dashboard -----
    dashboard_settings = DashboardSettings()  # pyright: ignore[reportCallIssue]
    dashboard_probe = HealthProbeClient(
        timeout_s=dashboard_settings.dashboard_health_timeout_s,
    )
    dashboard_service = DashboardService(
        probe_client=dashboard_probe,
        service_urls={
            "core": dashboard_settings.core_self_url,
        },
        langfuse_ui_url=dashboard_settings.langfuse_link(),
    )

    return CoreRuntime(
        db=db,
        user_service=user_service,
        auth_service=auth_service,
        auth_settings=auth_settings,
        background_tasks=BackgroundTaskSupervisor(),
        graph_settings=graph_settings,
        model_id_settings=model_id_settings,
        model_registry=model_registry,
        memory_runtime=memory_runtime,
        artifacts_runtime=artifacts_runtime,
        dashboard_service=dashboard_service,
        assistant_service=assistant_service,
        mcp_subscription_repository=mcp_subscription_repository,
        mcp_management_service=mcp_management_service,
        mcp_oauth_service=mcp_oauth_service,
        mcp_resolve_service=mcp_resolve_service,
        public_base_url=public_base_url,
    )
