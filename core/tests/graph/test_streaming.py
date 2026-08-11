"""Тесты graph.streaming: маппинг custom-чанков, сборка ответа, escape-исключения."""

from typing import Any
from unittest.mock import MagicMock
from uuid import uuid4

from langgraph.errors import GraphRecursionError
import pytest

from bestfiend.contracts.artifacts import ArtifactRef
from bestfiend.contracts.events import (
    AnswerDelta,
    AnswerFinal,
    AnswerReset,
    InputEvent,
    ProgressStep,
)
from bestfiend.graph.nodes.error.messages import STATIC_TEXTS
from bestfiend.graph.stream_keys import (
    ANSWER_DELTA_KEY,
    ANSWER_RESET_KEY,
    PROGRESS_STEP_KEY,
)
from bestfiend.graph.streaming import invoke_graph
from tests.graph.fakes import StreamPublisherFake


def _astream_with(*events: tuple[str, Any]) -> MagicMock:
    """Mock `graph.astream` — на каждый вызов возвращает async generator с `events`."""

    async def _gen(*args: Any, **kwargs: Any) -> Any:
        for ev in events:
            yield ev

    return MagicMock(side_effect=_gen)


def _event() -> InputEvent:
    return InputEvent(
        user_id=uuid4(),
        message="привет",
        channel="telegram",
        request_id="req-1",
    )


async def _invoke(
    graph: Any, pub: StreamPublisherFake, handler_provider: Any = None
) -> tuple[dict[str, Any], str] | None:
    return await invoke_graph(
        MagicMock(),
        _event(),
        MagicMock(),
        graph=graph,
        publisher=pub,  # type: ignore[arg-type]
        recursion_limit=100,
        langfuse_handler_provider=handler_provider,
    )


def _patch_langfuse(monkeypatch) -> None:
    monkeypatch.setattr("bestfiend.graph.streaming.get_client", lambda: MagicMock())


@pytest.mark.asyncio
async def test_invoke_graph_happy(monkeypatch) -> None:
    """astream → final values → AnswerFinal + close; возврат (final, text)."""
    _patch_langfuse(monkeypatch)
    pub = StreamPublisherFake()
    graph = MagicMock()
    graph.astream = _astream_with(("values", {"result": "hi"}))

    res = await _invoke(graph, pub)

    assert res is not None
    final, answer = res
    assert answer == "hi"
    assert final["result"] == "hi"
    assert len(pub.published) == 1
    assert pub.published[0].text == "hi"
    assert pub.closed == ["req-1"]


@pytest.mark.asyncio
async def test_invoke_graph_publishes_presented_as_attachments(monkeypatch) -> None:
    """final.presented_artifacts → AnswerFinal.attachments."""
    _patch_langfuse(monkeypatch)
    pub = StreamPublisherFake()
    ref = ArtifactRef(
        artifact_id="a1",
        type="document",
        artifact_user_name="r.md",
        storage_key="u/a1/data",
    )
    graph = MagicMock()
    graph.astream = _astream_with(
        ("values", {"result": "hi", "presented_artifacts": [ref]})
    )

    await _invoke(graph, pub)

    final_ev = pub.published[0]
    assert isinstance(final_ev, AnswerFinal)
    assert final_ev.attachments == [ref]


@pytest.mark.asyncio
async def test_invoke_graph_passes_langfuse_callbacks(monkeypatch) -> None:
    """Провайдер задан → handler уходит в config['callbacks'] вызова графа."""
    _patch_langfuse(monkeypatch)
    handler = object()
    graph = MagicMock()
    graph.astream = _astream_with(("values", {"result": "hi"}))

    await _invoke(graph, StreamPublisherFake(), handler_provider=lambda: handler)

    config = graph.astream.call_args.kwargs["config"]
    assert config["callbacks"] == [handler]
    assert config["recursion_limit"] == 100


@pytest.mark.asyncio
async def test_invoke_graph_no_provider_no_callbacks(monkeypatch) -> None:
    """Провайдер не задан → ключа 'callbacks' в config нет."""
    _patch_langfuse(monkeypatch)
    graph = MagicMock()
    graph.astream = _astream_with(("values", {"result": "hi"}))

    await _invoke(graph, StreamPublisherFake())

    assert "callbacks" not in graph.astream.call_args.kwargs["config"]


@pytest.mark.asyncio
async def test_invoke_graph_raising_provider_publishes_static_and_closes(
    monkeypatch,
) -> None:
    """Сбой langfuse-провайдера → static AnswerFinal + close, не подвисший стрим."""
    _patch_langfuse(monkeypatch)
    pub = StreamPublisherFake()
    graph = MagicMock()
    graph.astream = _astream_with(("values", {"result": "hi"}))

    def _boom() -> Any:
        raise RuntimeError("langfuse provider down")

    res = await _invoke(graph, pub, handler_provider=_boom)

    assert res is None
    assert pub.published[0].text == STATIC_TEXTS["unexpected"]
    assert pub.closed == ["req-1"]


@pytest.mark.asyncio
async def test_invoke_graph_empty_result_uses_static(monkeypatch) -> None:
    """Пустой result + пустой стрим → publish и persist получают STATIC_TEXTS[unexpected]."""
    _patch_langfuse(monkeypatch)
    pub = StreamPublisherFake()
    graph = MagicMock()
    graph.astream = _astream_with(("values", {"result": ""}))

    res = await _invoke(graph, pub)

    assert res is not None
    _, answer = res
    assert answer == STATIC_TEXTS["unexpected"]
    assert pub.published[0].text == STATIC_TEXTS["unexpected"]


@pytest.mark.asyncio
async def test_invoke_graph_recursion_escape(monkeypatch) -> None:
    """GraphRecursionError escape → static unexpected + close, возврат None."""
    _patch_langfuse(monkeypatch)
    pub = StreamPublisherFake()
    graph = MagicMock()
    graph.astream = MagicMock(side_effect=GraphRecursionError("limit"))

    res = await _invoke(graph, pub)

    assert res is None
    assert pub.published[0].text == STATIC_TEXTS["unexpected"]
    assert pub.closed == ["req-1"]


@pytest.mark.asyncio
async def test_invoke_graph_timeout_escape(monkeypatch) -> None:
    """TimeoutError escape → static provider_down."""
    _patch_langfuse(monkeypatch)
    pub = StreamPublisherFake()
    graph = MagicMock()
    graph.astream = MagicMock(side_effect=TimeoutError())

    res = await _invoke(graph, pub)

    assert res is None
    assert pub.published[0].text == STATIC_TEXTS["provider_down"]


@pytest.mark.asyncio
async def test_invoke_graph_maps_progress_step_key_to_progress_step(
    monkeypatch,
) -> None:
    """custom PROGRESS_STEP_KEY → publisher получает ProgressStep до AnswerFinal."""
    _patch_langfuse(monkeypatch)
    pub = StreamPublisherFake()
    graph = MagicMock()
    graph.astream = _astream_with(
        ("custom", {PROGRESS_STEP_KEY: "вызываю search"}),
        ("values", {"result": "ok"}),
    )

    await _invoke(graph, pub)

    types_in_order = [type(e).__name__ for e in pub.published]
    assert types_in_order == ["ProgressStep", "AnswerFinal"]
    progress = pub.published[0]
    assert isinstance(progress, ProgressStep)
    assert progress.text == "вызываю search"


@pytest.mark.asyncio
async def test_invoke_graph_reset_drops_preface_from_answer(
    monkeypatch,
) -> None:
    """preface → reset → final: answer_text = только пост-reset сегмент; AnswerReset опубликован."""
    _patch_langfuse(monkeypatch)
    pub = StreamPublisherFake()
    graph = MagicMock()
    graph.astream = _astream_with(
        ("custom", {ANSWER_DELTA_KEY: "сейчас поищу"}),
        ("custom", {PROGRESS_STEP_KEY: "вызываю search"}),
        ("custom", {ANSWER_RESET_KEY: ""}),
        ("custom", {ANSWER_DELTA_KEY: "финал"}),
        ("values", {"result": "финал"}),
    )

    res = await _invoke(graph, pub)

    assert res is not None
    _, answer = res
    assert answer == "финал"
    assert any(isinstance(e, AnswerReset) for e in pub.published)
    finals = [e for e in pub.published if isinstance(e, AnswerFinal)]
    assert len(finals) == 1
    assert finals[0].text == "финал"
    deltas = [e for e in pub.published if isinstance(e, AnswerDelta)]
    assert [d.delta for d in deltas] == ["сейчас поищу", "финал"]


@pytest.mark.asyncio
async def test_invoke_graph_reset_without_final_falls_back_to_result(
    monkeypatch,
) -> None:
    """reset без последующего ANSWER_DELTA → answer_text fallback на state.result."""
    _patch_langfuse(monkeypatch)
    pub = StreamPublisherFake()
    graph = MagicMock()
    graph.astream = _astream_with(
        ("custom", {ANSWER_DELTA_KEY: "preface"}),
        ("custom", {ANSWER_RESET_KEY: ""}),
        ("values", {"result": "из result"}),
    )

    res = await _invoke(graph, pub)

    assert res is not None
    _, answer = res
    assert answer == "из result"
