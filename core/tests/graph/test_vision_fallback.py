"""Тесты vision-фолбэка: модель без зрения получает пометку вместо image-блоков."""

from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from langchain_core.messages import AIMessage, HumanMessage, messages_to_dict
import pytest

from bestfiend.contracts.artifacts import ArtifactRef
from bestfiend.contracts.events import InputEvent
from bestfiend.control_plane.model_registry.contracts import ResolveModelResponse
from bestfiend.graph.attached_artifacts import (
    VISION_FALLBACK_NOTE,
    annotate_unsupported_images,
    enrich_human_with_artifacts,
    strip_image_blocks,
)
from bestfiend.graph.config import GraphSettings, ModelIDSettings
from bestfiend.graph.runtime import GraphRuntime
from bestfiend.primitives.background_tasks import BackgroundTaskSupervisor
from tests.graph.fakes import StreamPublisherFake


def _image_ref(artifact_id: str = "id-aaa") -> ArtifactRef:
    return ArtifactRef(
        artifact_id=artifact_id,
        artifact_user_name="photo.jpg",
        type="image",
        description="",
    )


def _table_ref() -> ArtifactRef:
    return ArtifactRef(
        artifact_id="id-tbl",
        artifact_user_name="report.csv",
        type="table",
        description="",
    )


def _text_blocks(content: object) -> list[str]:
    assert isinstance(content, list)
    return [
        block["text"]
        for block in content
        if isinstance(block, dict) and block.get("type") == "text"
    ]


# ── annotate_unsupported_images ───────────────────────────────────────


@pytest.mark.asyncio
async def test_annotate_appends_note_after_original_text() -> None:
    """Текущий ход с image-рефом: блок №0 — исходный текст, блок №1 — пометка."""
    human = enrich_human_with_artifacts("что на фото?", [_image_ref()])
    original_text = human.content

    out = await annotate_unsupported_images([human])

    assert _text_blocks(out[0].content) == [original_text, VISION_FALLBACK_NOTE]


@pytest.mark.asyncio
async def test_annotate_skips_human_without_image_refs() -> None:
    """Без image-рефов (в том числе с рефами других типов) лента не меняется."""
    plain = HumanMessage(content="просто текст")
    with_table = enrich_human_with_artifacts("что в файле?", [_table_ref()])

    assert await annotate_unsupported_images([plain]) == [plain]
    assert await annotate_unsupported_images([with_table]) == [with_table]


@pytest.mark.asyncio
async def test_annotate_ignores_image_refs_in_history() -> None:
    """Исторический Human с рефами уже отвечен — пометка только на текущем ходе."""
    old = enrich_human_with_artifacts("старое фото", [_image_ref("id-old")])
    current = enrich_human_with_artifacts("новое фото", [_image_ref("id-new")])
    lane = [old, AIMessage(content="ответ"), current]

    out = await annotate_unsupported_images(lane)

    assert out[0].content == old.content  # история — str, без пометки
    assert _text_blocks(out[2].content)[1] == VISION_FALLBACK_NOTE


@pytest.mark.asyncio
async def test_annotate_noop_when_last_is_not_human() -> None:
    """Последнее сообщение не Human (или ленты нет) → возвращается как есть."""
    human = enrich_human_with_artifacts("фото", [_image_ref()])
    ai = AIMessage(content="ответ")

    assert await annotate_unsupported_images([human, ai]) == [human, ai]
    assert await annotate_unsupported_images([]) == []


@pytest.mark.asyncio
async def test_strip_after_annotate_restores_exact_dump() -> None:
    """Persist-инвариант: пометка volatile, dict-дамп после strip идентичен исходному."""
    human = enrich_human_with_artifacts("что на фото?", [_image_ref()])
    lane = [human, AIMessage(content="ответ"), human]
    dump_before = messages_to_dict(lane)

    annotated = await annotate_unsupported_images(lane)
    assert isinstance(annotated[-1].content, list)  # аннотация реально случилась

    assert messages_to_dict(strip_image_blocks(annotated)) == dump_before


# ── _build_image_hydrator: выбор обработчика по конфигу ───────────────


def _runtime(*, image_bytes: bytes = b"jpg-bytes") -> GraphRuntime:
    artifacts = MagicMock()
    artifacts.read_bytes_for_user = AsyncMock(return_value=image_bytes)
    return GraphRuntime(
        stream_publisher=StreamPublisherFake(),  # type: ignore[arg-type]
        graph=MagicMock(),
        settings=GraphSettings(),  # pyright: ignore[reportCallIssue]
        model_registry=AsyncMock(),
        memory_runtime=MagicMock(),
        artifacts=artifacts,
        background_tasks=BackgroundTaskSupervisor(),
        mcp_server_resolver=None,
        model_id_settings=ModelIDSettings(model_id="test-model"),
        langfuse_handler_provider=None,
    )


def _event() -> InputEvent:
    return InputEvent(
        user_id=uuid4(),
        message="что на фото?",
        channel="telegram",
        request_id="req-1",
    )


def _rc(*, supports_vision: bool) -> ResolveModelResponse:
    config: dict[str, Any] = {
        "provider": "openai",
        "model": "x",
        "supports_vision": supports_vision,
    }
    return ResolveModelResponse(config=config)


@pytest.mark.asyncio
async def test_build_hydrator_without_vision_annotates() -> None:
    """supports_vision=false → не None, а обработчик, который вешает пометку."""
    hydrator = _runtime()._build_image_hydrator(_event(), _rc(supports_vision=False))
    assert hydrator is not None

    out = await hydrator([enrich_human_with_artifacts("фото", [_image_ref()])])

    assert _text_blocks(out[0].content)[1] == VISION_FALLBACK_NOTE


@pytest.mark.asyncio
async def test_build_hydrator_with_vision_still_builds_image_blocks() -> None:
    """supports_vision=true → прежнее поведение: нативные image-блоки, без пометки."""
    hydrator = _runtime()._build_image_hydrator(_event(), _rc(supports_vision=True))
    assert hydrator is not None

    out = await hydrator([enrich_human_with_artifacts("фото", [_image_ref()])])

    content = out[0].content
    assert isinstance(content, list)
    assert content[1]["type"] == "image"  # type: ignore[index]
    assert VISION_FALLBACK_NOTE not in _text_blocks(content)


def test_build_hydrator_malformed_config_returns_none() -> None:
    """Кривой конфиг модели → None: запрос всё равно завернёт _build_context."""
    rt = _runtime()

    assert rt._build_image_hydrator(_event(), ResolveModelResponse(config={})) is None


@pytest.mark.asyncio
async def test_fallback_hydrator_reaches_subtask_context(monkeypatch) -> None:
    """Тот же объект-обработчик уходит в ctx — фолбэк доезжает до delegate_subtask."""
    rt = _runtime()
    monkeypatch.setattr(
        "bestfiend.graph.runtime.build_chat_model", lambda cfg: MagicMock()
    )
    event = _event()
    hydrator = rt._build_image_hydrator(event, _rc(supports_vision=False))

    ctx = await rt._build_context(event, _rc(supports_vision=False), {}, {}, hydrator)

    assert ctx is not None
    assert ctx.hydrate_images is hydrator is annotate_unsupported_images
