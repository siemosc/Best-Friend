"""GraphRuntime — обвязка LangGraph поверх скомпилированного state-graph'а.

Оркестрация одного InputEvent: резолв модели, параллельные memory-fetch и
MCP-discovery, сборка OrchestrationState/GraphContext, запуск astream-цикла
(`graph.streaming`) и фоновый персист хода (`graph.persist`).
"""

import asyncio
from collections.abc import Callable
from typing import Any
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import BaseMessage, HumanMessage
from langchain_core.tools import StructuredTool
from langgraph.graph.state import CompiledStateGraph
from loguru import logger

from bestfiend.ai.config import AIConfig
from bestfiend.ai.errors import AIConfigError
from bestfiend.ai.llm import build_chat_model
from bestfiend.artifacts.service import ArtifactService
from bestfiend.contracts.events import (
    InputEvent,
    OutboundEventPublisher,
    ProgressStep,
)
from bestfiend.contracts.mcp import McpServerResolver
from bestfiend.control_plane.model_registry import ModelRegistry
from bestfiend.control_plane.model_registry.contracts import (
    ResolveModelRequest,
    ResolveModelResponse,
)
from bestfiend.graph.attached_artifacts import (
    HydrateImages,
    annotate_unsupported_images,
    enrich_human_with_artifacts,
    hydrate_image_artifacts,
)
from bestfiend.graph.config import GraphSettings, ModelIDSettings
from bestfiend.graph.context import GraphContext
from bestfiend.graph.nodes.error.messages import STATIC_TEXTS
from bestfiend.graph.persist import persist_turn
from bestfiend.graph.state import InputContext, OrchestrationState, ToolServerEntryView
from bestfiend.graph.streaming import invoke_graph, publish_final
from bestfiend.graph.tool_builder import build_mcp_tools
from bestfiend.mcp.contracts import DiscoveryFailure
from bestfiend.mcp.discovery import discover_servers
from bestfiend.mcp.settings import McpDiscoverySettings
from bestfiend.memory.agent_tools import MEMORY_TOOL_NAMES, build_memory_tools
from bestfiend.memory.budget import ReadBudget, plan_read_budget
from bestfiend.memory.contracts import MemoryContext
from bestfiend.memory.runtime import MemoryRuntime
from bestfiend.memory.search_pipeline import search as memory_search
from bestfiend.primitives.background_tasks import BackgroundTaskSupervisor
from bestfiend.primitives.tokenizer import count_tokens


class GraphRuntime:
    """Запускает LangGraph для одного InputEvent.

    Ответственность:
    1. Preprocessing: InputEvent + ResolveModelResponse → State, GraphContext.
    2. Параллельные memory-fetch и MCP-discovery.
    3. Оркестрация запуска: astream-цикл (`graph.streaming`) + фоновый
       персист хода (`graph.persist`).
    """

    def __init__(
        self,
        *,
        stream_publisher: OutboundEventPublisher,
        graph: CompiledStateGraph[Any, Any, Any, Any],
        settings: GraphSettings,
        model_registry: ModelRegistry,
        memory_runtime: MemoryRuntime,
        artifacts: ArtifactService,
        background_tasks: BackgroundTaskSupervisor,
        mcp_server_resolver: McpServerResolver | None = None,
        mcp_discovery_settings: McpDiscoverySettings | None = None,
        model_id_settings: ModelIDSettings | None = None,
        langfuse_handler_provider: Callable[[], BaseCallbackHandler | None]
        | None = None,
    ) -> None:
        """Инициализация runtime."""
        self._stream_publisher = stream_publisher
        self._graph = graph
        self._settings = settings
        self._model_registry = model_registry
        self._memory_runtime = memory_runtime
        self._artifacts = artifacts
        self._background_tasks = background_tasks
        self._mcp_server_resolver = mcp_server_resolver
        self._mcp_discovery_settings = mcp_discovery_settings or McpDiscoverySettings()  # pyright: ignore[reportCallIssue]
        self._model_id_settings = model_id_settings or ModelIDSettings()  # pyright: ignore[reportCallIssue]
        # Ленивый провайдер langfuse CallbackHandler: handler берёт актуальный
        # Langfuse singleton на момент вызова (GraphRuntime собирается до init
        # Langfuse). Через него langchain/langgraph эмитят спаны нод и LLM-генераций.
        self._langfuse_handler_provider = langfuse_handler_provider

    async def process_input_event(self, event: InputEvent) -> None:
        """Запускает граф для InputEvent; доставляет финал и персистит ход.

        Caller (ingress handler) уже открыл подписку через
        `stream_publisher.open(event.request_id)`. Здесь публикуем финал в неё
        и закрываем (sentinel), чтобы consumer завершил итерацию.
        """
        logger.info(
            "GraphRuntime: input event mode={} request_id={}",
            event.processing_mode,
            event.request_id,
        )

        rc = await self._resolve_request_config(event.user_id, event)
        if rc is None:
            # _resolve_request_config уже опубликовал AnswerFinal и закрыл стрим.
            return

        # Память и MCP discovery — параллельно: сетевой вызов эмбеддера recall
        # маскируется временем discovery (оба идут на каждый запрос).
        budget = self._plan_budget(rc, event)
        memory_task = asyncio.create_task(self._fetch_memory(event, budget))
        tools, catalog, serial, failures = await self._discover_mcp(event)
        memory = await memory_task
        await self._notify_discovery_failures(event.request_id, failures)

        state = self._build_state(event, rc, catalog, memory)
        hydrate_images = self._build_image_hydrator(event, rc)
        if hydrate_images is not None:
            # После планирования бюджета: математика памяти остаётся на тексте,
            # image-блоки (или vision-пометка) — поверх. Перед персистом снимет strip.
            state.stm = await hydrate_images(state.stm)
        ctx = await self._build_context(event, rc, tools, serial, hydrate_images)
        if ctx is None:
            # _build_context уже опубликовал static AnswerFinal и закрыл стрим.
            return

        res = await invoke_graph(
            state,
            event,
            ctx,
            graph=self._graph,
            publisher=self._stream_publisher,
            recursion_limit=self._settings.graph_recursion_limit,
            langfuse_handler_provider=self._langfuse_handler_provider,
        )
        if res is not None:
            final, answer_text = res
            # Срез текущего turn'а из stm (от Human-маркера до конца) → persist.
            turn_start = final.get("turn_start_index", 0)
            turn_messages = final.get("stm", [])[turn_start:]
            self._background_tasks.create_task(
                persist_turn(
                    event,
                    turn_messages=turn_messages,
                    answer_text=answer_text,
                    memory_runtime=self._memory_runtime,
                ),
                name=f"persist-turn:{event.request_id}",
            )

    def _build_image_hydrator(
        self,
        event: InputEvent,
        rc: ResolveModelResponse,
    ) -> HydrateImages | None:
        """Pre-bound обработка картинок для этого запроса; None — конфиг нечитаем.

        Модель с vision получает нативные image-блоки: замыкаются artifacts +
        user_id сессии (ключ строится из него, хранёному не доверяем) + лимиты из
        settings. Модель без vision — фолбэк-аннотатор: вместо картинки в текущий
        ход кладётся пометка, что изображение пришло, но не может быть просмотрено.
        Один и тот же обработчик работает для top-level ленты и для seed'а
        субагента (модель одна на весь граф).
        """
        try:
            supports_vision = AIConfig(rc.config).supports_vision
        except AIConfigError:
            return (
                None  # кривой конфиг завернёт _build_context, здесь просто без vision
            )
        if not supports_vision:
            return annotate_unsupported_images

        async def read_image(artifact_id: str) -> bytes:
            return await self._artifacts.read_bytes_for_user(event.user_id, artifact_id)

        async def hydrate(messages: list[BaseMessage]) -> list[BaseMessage]:
            return await hydrate_image_artifacts(
                messages,
                read_image=read_image,
                max_history_images=self._settings.vision_max_history_images,
                max_image_bytes=self._settings.vision_max_image_bytes,
            )

        return hydrate

    async def _build_context(
        self,
        event: InputEvent,
        rc: ResolveModelResponse,
        tools: dict[str, StructuredTool],
        serial_tool_servers: dict[str, str],
        hydrate_images: HydrateImages | None,
    ) -> GraphContext | None:
        """Собирает per-request GraphContext: единая модель графа, тулзы, само-ссылку.

        Конфиг модели обязателен — пустой/кривой → static AnswerFinal + None
        (граф без модели не запустить). Тулзы памяти замкнуты на user_id запроса;
        для субагентов они скрыты через top_level_only_tool_names.
        """
        request_id = event.request_id
        if not rc.config:
            logger.error("GraphRuntime: empty model config request_id={}", request_id)
            await publish_final(
                self._stream_publisher, request_id, STATIC_TEXTS["provider_down"]
            )
            return None
        try:
            model = build_chat_model(rc.config)
        except Exception as exc:
            logger.error("GraphRuntime: build_chat_model failed: {}", exc)
            await publish_final(
                self._stream_publisher, request_id, STATIC_TEXTS["provider_down"]
            )
            return None

        memory_tools = build_memory_tools(self._memory_runtime, event.user_id)
        logger.info(
            "GraphRuntime: context built request_id={} model={}:{} base={}",
            request_id,
            rc.config.get("provider"),
            rc.config.get("model"),
            rc.config.get("api_base"),
        )
        return GraphContext(
            model=model,
            tools_by_name={**tools, **memory_tools},
            top_level_only_tool_names=MEMORY_TOOL_NAMES,
            serial_tool_servers=serial_tool_servers,
            graph=self._graph,
            hydrate_images=hydrate_images,
            soft_gate_limit=self._settings.soft_gate_limit,
            max_recursion_depth=self._settings.max_recursion_depth,
            child_recursion_limit=self._settings.child_recursion_limit,
        )

    def _build_state(
        self,
        event: InputEvent,
        rc: ResolveModelResponse,
        catalog: list[ToolServerEntryView],
        memory: MemoryContext,
    ) -> OrchestrationState:
        """Preprocessing: InputEvent + ResolveModelResponse + память → OrchestrationState."""
        # log_tail всегда оканчивается текущим Human (turn_start_index = len-1).
        # Свой Human тут не добавляем — дубль.
        stm = list(memory.log_tail)
        if event.attached_artifacts and stm:
            # Приложенные файлы впечатываем в текущий Human (последний элемент ленты):
            # модель видит их сразу и во всех следующих ходах (персист 1-в-1 в лог).
            stm[-1] = enrich_human_with_artifacts(
                event.message, event.attached_artifacts
            )

        return OrchestrationState(
            input=InputContext(
                message=event.message,
                request_id=event.request_id,
                attached_artifacts=event.attached_artifacts,
                user_environment=rc.user_environment,
                user_instruction=rc.user_instruction or "",
                journal=memory.journal,
                profile=memory.profile,
                recall=memory.recall,
                tool_catalog=catalog,
            ),
            stm=stm,
            turn_start_index=max(len(stm) - 1, 0),
            processing_mode=event.processing_mode,
        )

    async def _discover_mcp(
        self, event: InputEvent
    ) -> tuple[
        dict[str, StructuredTool],
        list[ToolServerEntryView],
        dict[str, str],
        list[tuple[str, DiscoveryFailure]],
    ]:
        """Резолв серверов юзера → live-discovery → тулы + каталог + serial-map + фейлы.

        Без кэша: опрос на каждый запрос. Сбой storage → graceful (граф без тулов).
        Фейлы отдельных серверов изолированы в discover_servers, остальные работают.
        `serial-map` — namespaced tool → connection_id для серверов без параллельности.
        """
        if self._mcp_server_resolver is None:
            return {}, [], {}, []
        try:
            resolved = await self._mcp_server_resolver.list_for_user(event.user_id)
        except Exception as exc:  # noqa: BLE001 — fail-soft: сбой резолва → граф без тулов
            logger.warning(
                "GraphRuntime: mcp resolve failed, proceeding without tools: {}", exc
            )
            return {}, [], {}, []
        if not resolved:
            return {}, [], {}, []
        discoveries = await discover_servers(resolved, self._mcp_discovery_settings)
        tools, catalog, serial = build_mcp_tools(
            resolved, discoveries, _build_request_meta(event)
        )
        failures = [(d.name, d.failure) for d in discoveries if d.failure is not None]
        logger.info(
            "GraphRuntime: mcp discovery request_id={} servers={} tools={} "
            "serial={} failures={}",
            event.request_id,
            len(resolved),
            len(tools),
            len(serial),
            len(failures),
        )
        return tools, catalog, serial, failures

    async def _notify_discovery_failures(
        self, request_id: str, failures: list[tuple[str, DiscoveryFailure]]
    ) -> None:
        """Шлёт ProgressStep на каждый недоступный сервер (тот же путь, что прогресс-шаги)."""
        for name, failure in failures:
            await self._stream_publisher.publish(
                ProgressStep(
                    request_id=request_id,
                    text=f"⚠️ MCP-сервер {name} недоступен ({failure.kind})",
                )
            )

    async def _fetch_memory(
        self, event: InputEvent, budget: ReadBudget
    ) -> MemoryContext:
        """Загружает контекст памяти из in-process memory pipeline.

        При исключении — fallback на ленту из одного текущего Human (граф
        продолжает без истории и блоков памяти).
        """
        try:
            return await memory_search(
                event.user_id,
                self._memory_runtime,
                event.message,
                budget,
                self._background_tasks,
            )
        except Exception as exc:
            logger.warning(
                "GraphRuntime: memory search failed, proceeding without: {}", exc
            )
            return MemoryContext(log_tail=[HumanMessage(content=event.message)])

    def _plan_budget(self, rc: ResolveModelResponse, event: InputEvent) -> ReadBudget:
        """Read-бюджет памяти из окна резолвнутой модели графа и размера входа.

        Кривой config окна → дефолты: бюджет не валит запрос (fail-open).
        """
        settings = self._memory_runtime.memory_settings
        window = settings.ctx_default_window
        reserve = settings.ctx_default_reserve
        try:
            config = AIConfig(rc.config)
            window = config.context_window or window
            reserve = config.max_tokens or reserve
        except AIConfigError as exc:
            logger.warning(
                "GraphRuntime: budget config invalid, using defaults: {}", exc
            )
        return plan_read_budget(window, reserve, count_tokens(event.message), settings)

    def _build_resolve_request(self, user_id: UUID) -> ResolveModelRequest:
        """Строит ResolveModelRequest из ModelIDSettings + user_id."""
        return ResolveModelRequest(
            model_id=self._model_id_settings.model_id,
            user_id=user_id,
        )

    async def _resolve_request_config(
        self,
        user_id: UUID,
        request: InputEvent,
    ) -> ResolveModelResponse | None:
        """Резолвит модельные конфиги через in-process ModelRegistry.

        При ошибке — log + публикация AnswerFinal с фолбэк-текстом + close
        стрима + None. Стрим обязан закрыться, иначе consumer подписки зависнет.
        """
        try:
            resolve_req = self._build_resolve_request(user_id)
            return await self._model_registry.resolve(resolve_req)
        except Exception as exc:
            logger.error(
                "GraphRuntime: request_config lookup failed user_id={}: {}",
                user_id,
                exc,
            )
            await publish_final(
                self._stream_publisher,
                request.request_id,
                "Сервис временно недоступен. Попробуй чуть позже.",
            )
            return None


def _build_request_meta(event: InputEvent) -> dict[str, str]:
    """MCP `_meta`-payload, broadcast на все серверы юзера. СЕЙЧАС только user_id (timezone — отд. задача)."""
    return {"user_id": str(event.user_id)}
