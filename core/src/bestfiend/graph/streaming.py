"""astream-цикл графа: custom-чанки → live-события стрима, финал — AnswerFinal.

Producer-ноды пишут custom-чанки в `runtime.stream_writer` под ключами
`ANSWER_DELTA_KEY`/`PROGRESS_STEP_KEY`/`ANSWER_RESET_KEY` — здесь они мапятся
в `AnswerDelta`/`ProgressStep`/`AnswerReset` и публикуются в
`OutboundEventPublisher`.
"""

import asyncio
from collections.abc import Callable
from typing import Any, cast

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.runnables import RunnableConfig
from langfuse import get_client
from langgraph.errors import GraphBubbleUp
from langgraph.graph.state import CompiledStateGraph
from loguru import logger

from bestfiend.contracts.artifacts import ArtifactRef
from bestfiend.contracts.events import (
    AnswerDelta,
    AnswerFinal,
    AnswerReset,
    InputEvent,
    OutboundEventPublisher,
    ProgressStep,
)
from bestfiend.graph.context import GraphContext
from bestfiend.graph.nodes.error.messages import STATIC_TEXTS
from bestfiend.graph.state import OrchestrationState
from bestfiend.graph.stream_keys import (
    ANSWER_DELTA_KEY,
    ANSWER_RESET_KEY,
    PROGRESS_STEP_KEY,
)


async def publish_final(
    publisher: OutboundEventPublisher,
    request_id: str,
    text: str,
    attachments: list[ArtifactRef] | None = None,
) -> None:
    """Публикует AnswerFinal (с опц. attachments) и закрывает стрим."""
    await publisher.publish(
        AnswerFinal(request_id=request_id, text=text, attachments=attachments or [])
    )
    await publisher.close(request_id)


def _static_kind(exc: BaseException) -> str:
    """Escaping-исключение → ключ STATIC_TEXTS (тонкий внешний net)."""
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return "provider_down"
    return "unexpected"


async def invoke_graph(
    state: OrchestrationState,
    event: InputEvent,
    ctx: GraphContext,
    *,
    graph: CompiledStateGraph[Any, Any, Any, Any],
    publisher: OutboundEventPublisher,
    recursion_limit: int,
    langfuse_handler_provider: Callable[[], BaseCallbackHandler | None] | None,
) -> tuple[dict[str, Any], str] | None:
    """`astream` графа + live-доставка AnswerDelta/ProgressStep + финал AnswerFinal.

    Producer пишет custom-чанки в `runtime.stream_writer` под ключами
    `ANSWER_DELTA_KEY`/`PROGRESS_STEP_KEY` — мапим их в `OutboundEvent` и
    публикуем. `final` собирается с последнего `mode == "values"` payload'а.

    `answer_text = "".join(streamed)` — это то, что пользователь видел в
    draft; на сценарии «preface + tool + final» `state.result` содержит
    только последний react-шаг, а streamed — всё с первого, включая preface.
    Fallback на `state.result` — если стрим почему-то пуст.

    Внешний try — тонкий net для escape-исключений из `astream` (memory:
    langgraph 1.2.1 custom-stream бывает пропускает оригинальное исключение
    мимо error-ноды); на escape публикуем static-текст.
    """
    request_id = event.request_id
    try:
        return await _invoke_graph_once(
            state=state,
            event=event,
            ctx=ctx,
            graph=graph,
            publisher=publisher,
            recursion_limit=recursion_limit,
            langfuse_handler_provider=langfuse_handler_provider,
        )
    except asyncio.CancelledError:
        logger.info(
            "graph.streaming: graph cancelled (client disconnect?) request_id={}",
            request_id,
        )
        await publisher.close(request_id)
        raise
    except GraphBubbleUp:
        raise
    except Exception as exc:
        logger.exception("graph.streaming: graph invocation failed: {}", exc)
        text = STATIC_TEXTS.get(_static_kind(exc), STATIC_TEXTS["unexpected"])
        await publish_final(publisher, request_id, text)
        return None


async def _invoke_graph_once(
    *,
    state: OrchestrationState,
    event: InputEvent,
    ctx: GraphContext,
    graph: CompiledStateGraph[Any, Any, Any, Any],
    publisher: OutboundEventPublisher,
    recursion_limit: int,
    langfuse_handler_provider: Callable[[], BaseCallbackHandler | None] | None,
) -> tuple[dict[str, Any], str]:
    """Выполняет один traced-вызов графа и публикует успешный финал."""
    request_id = event.request_id
    with get_client().start_as_current_observation(
        name="Graph.invoke",
        as_type="span",
        metadata={"user_id": str(event.user_id), "request_id": request_id},
    ) as span:
        # callbacks внутри span'а Graph.invoke → langgraph/langchain спаны
        # вкладываются под него (handler парентит к текущему OTel-спану).
        # Резолв провайдера — внутри защищённой зоны invoke_graph: его сбой
        # уходит в static-финал, а не подвешивает consumer без AnswerFinal/close.
        config = _graph_config(recursion_limit, langfuse_handler_provider)
        final, streamed_chunks = await _consume_graph_stream(
            state=state,
            ctx=ctx,
            graph=graph,
            config=config,
            publisher=publisher,
            request_id=request_id,
        )
        answer_text = (
            "".join(streamed_chunks)
            or final.get("result")
            or STATIC_TEXTS["unexpected"]
        )
        presented = _presented_artifacts(final)
        await publish_final(publisher, request_id, answer_text, presented)
        logger.info(
            "graph.streaming: answer delivered request_id={} len={} streamed_chunks={}",
            request_id,
            len(answer_text),
            len(streamed_chunks),
        )
        span.update(output={"response_len": len(answer_text)})
        return final, answer_text


def _graph_config(
    recursion_limit: int,
    handler_provider: Callable[[], BaseCallbackHandler | None] | None,
) -> RunnableConfig:
    """Собирает конфиг графа с опциональным Langfuse callback."""
    config: RunnableConfig = {"recursion_limit": recursion_limit}
    handler = handler_provider() if handler_provider is not None else None
    if handler is not None:
        config["callbacks"] = [handler]
    return config


async def _consume_graph_stream(
    *,
    state: OrchestrationState,
    ctx: GraphContext,
    graph: CompiledStateGraph[Any, Any, Any, Any],
    config: RunnableConfig,
    publisher: OutboundEventPublisher,
    request_id: str,
) -> tuple[dict[str, Any], list[str]]:
    """Собирает values и публикует custom-события графа."""
    final: dict[str, Any] = {}
    streamed_chunks: list[str] = []
    # subgraphs=False по умолчанию → каждый yield = (mode, payload).
    # С subgraphs=True было бы (ns, mode, payload).
    async for mode, payload in graph.astream(
        state,
        context=ctx,
        config=config,
        stream_mode=["custom", "values"],
    ):
        if mode == "custom":
            await _publish_custom_chunk(
                cast("dict[str, Any]", payload),
                publisher=publisher,
                request_id=request_id,
                streamed_chunks=streamed_chunks,
            )
        elif mode == "values":
            final = cast("dict[str, Any]", payload)
    return final, streamed_chunks


async def _publish_custom_chunk(
    custom: dict[str, Any],
    *,
    publisher: OutboundEventPublisher,
    request_id: str,
    streamed_chunks: list[str],
) -> None:
    """Преобразует один custom-чанк в исходящие события."""
    for key, value in custom.items():
        if key == ANSWER_DELTA_KEY:
            streamed_chunks.append(value)
            await publisher.publish(AnswerDelta(request_id=request_id, delta=value))
        elif key == PROGRESS_STEP_KEY:
            await publisher.publish(ProgressStep(request_id=request_id, text=value))
        elif key == ANSWER_RESET_KEY:
            # Стримленный сегмент был preface — забываем его,
            # финал = только дельты после reset.
            streamed_chunks.clear()
            await publisher.publish(AnswerReset(request_id=request_id))


def _presented_artifacts(final: dict[str, Any]) -> list[ArtifactRef]:
    """Нормализует показанные графом артефакты."""
    return [
        ref if isinstance(ref, ArtifactRef) else ArtifactRef.model_validate(ref)
        for ref in (final.get("presented_artifacts") or [])
    ]
