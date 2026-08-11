"""Тесты ingress-рендера приложенных файлов, обогащения HumanMessage и гидрации."""

import base64

from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    messages_from_dict,
    messages_to_dict,
)
import pytest

from bestfiend.contracts.artifacts import ArtifactRef
from bestfiend.graph.attached_artifacts import (
    enrich_human_with_artifacts,
    hydrate_image_artifacts,
    render_attached_artifacts_md,
    strip_image_blocks,
)


def _ref(*, description: str = "Сводка за май") -> ArtifactRef:
    return ArtifactRef(
        artifact_id="abc123def456",
        artifact_user_name="report.csv",
        type="table",
        description=description,
        storage_key="u/abc123def456/data",
    )


def test_render_includes_header_name_and_description() -> None:
    md = render_attached_artifacts_md([_ref()])
    assert md.startswith("Приложенные файлы:")
    assert "`report_def456.csv`" in md  # artifact_llm_name = {stem}_{id[-6:]}{ext}
    assert "— Сводка за май" in md


def test_render_omits_dash_when_description_empty() -> None:
    md = render_attached_artifacts_md([_ref(description="")])
    assert "`report_def456.csv`" in md
    assert "—" not in md


def test_render_empty_list_is_empty() -> None:
    assert render_attached_artifacts_md([]) == ""


def test_enrich_keeps_user_text_and_appends_block() -> None:
    human = enrich_human_with_artifacts("что в файле?", [_ref()])
    assert isinstance(human, HumanMessage)
    assert isinstance(human.content, str)
    assert human.content.startswith("что в файле?")
    assert "Приложенные файлы:" in human.content


def test_enrich_stores_whitelisted_json_dicts_without_storage_key() -> None:
    human = enrich_human_with_artifacts("x", [_ref()])
    stored = human.additional_kwargs["attached_artifacts"]
    assert isinstance(stored, list)
    assert isinstance(stored[0], dict)  # JSON-safe dict, не объект ArtifactRef
    assert stored[0]["artifact_id"] == "abc123def456"
    assert stored[0]["artifact_user_name"] == "report.csv"
    # storage_key НЕ персистится: резолвер строит ключ из session user_id (защита от
    # доверия кросс-юзерному пути). art_meta — без читателя, тоже не храним.
    assert "storage_key" not in stored[0]
    assert "art_meta" not in stored[0]


def test_additional_kwargs_survives_messages_roundtrip() -> None:
    human = enrich_human_with_artifacts("x", [_ref()])
    restored = messages_from_dict(messages_to_dict([human]))[0]
    stored = restored.additional_kwargs["attached_artifacts"]
    assert stored[0]["artifact_id"] == "abc123def456"


# ── Гидрация image-блоков и обратный strip ─────────────────────────


def _image_ref(artifact_id: str, name: str = "photo.jpg") -> ArtifactRef:
    return ArtifactRef(
        artifact_id=artifact_id,
        artifact_user_name=name,
        type="image",
        description="",
    )


def _reads(payloads: dict[str, bytes]):
    """read_image из словаря artifact_id → bytes; неизвестный id кидает KeyError."""

    async def read_image(artifact_id: str) -> bytes:
        return payloads[artifact_id]

    return read_image


async def _hydrate(messages, read_image, *, cap: int = 6, max_bytes: int = 1000):
    return await hydrate_image_artifacts(
        messages,
        read_image=read_image,
        max_history_images=cap,
        max_image_bytes=max_bytes,
    )


@pytest.mark.asyncio
async def test_hydrate_current_turn_builds_v1_image_blocks() -> None:
    """Текущий ход (последний Human): текст первым блоком + v1 image-блоки."""
    human = enrich_human_with_artifacts(
        "что на фото?", [_image_ref("id-aaa"), _image_ref("id-bbb", "cat.png")]
    )
    original_text = human.content

    out = await _hydrate([human], _reads({"id-aaa": b"jpg-bytes", "id-bbb": b"png"}))

    content = out[0].content
    assert isinstance(content, list)
    assert content[0] == {"type": "text", "text": original_text}
    assert content[1] == {
        "type": "image",
        "base64": base64.b64encode(b"jpg-bytes").decode("ascii"),
        "mime_type": "image/jpeg",
    }
    png_block = content[2]
    assert isinstance(png_block, dict)
    assert png_block["mime_type"] == "image/png"


def _image_block_count(content: object) -> int:
    if not isinstance(content, list):
        return 0
    return sum(1 for b in content if isinstance(b, dict) and b.get("type") == "image")


@pytest.mark.asyncio
async def test_hydrate_history_capped_partial_boundary_message() -> None:
    """История от хвоста: граничное сообщение гидрируется частично, старше — текстом."""
    oldest = enrich_human_with_artifacts("самое старое", [_image_ref("id-0")])
    older = enrich_human_with_artifacts(
        "старое", [_image_ref("id-1"), _image_ref("id-2")]
    )
    newer = enrich_human_with_artifacts(
        "новее", [_image_ref("id-3"), _image_ref("id-4")]
    )
    current = HumanMessage(content="текущий без файлов")
    lane = [
        oldest,
        AIMessage(content="ответ"),
        older,
        AIMessage(content="ответ"),
        newer,
        AIMessage(content="ответ"),
        current,
    ]
    payloads = {f"id-{i}": b"x" for i in range(5)}

    out = await _hydrate(lane, _reads(payloads), cap=3)

    assert _image_block_count(out[4].content) == 2  # newer: целиком
    assert _image_block_count(out[2].content) == 1  # older: частично, остаток бюджета
    assert isinstance(out[0].content, str)  # oldest: бюджет исчерпан — текстом
    assert out[6].content == "текущий без файлов"


@pytest.mark.asyncio
async def test_hydrate_media_group_history_fills_cap() -> None:
    """Регрессия: media group из 10 фото в истории при капе 6 даёт ровно 6 блоков."""
    group = enrich_human_with_artifacts(
        "альбом", [_image_ref(f"id-{i}") for i in range(10)]
    )
    current = HumanMessage(content="текущий без файлов")
    lane = [group, AIMessage(content="ответ"), current]
    payloads = {f"id-{i}": b"x" for i in range(10)}

    out = await _hydrate(lane, _reads(payloads), cap=6)

    assert _image_block_count(out[0].content) == 6


@pytest.mark.asyncio
async def test_hydrate_ignores_non_image_refs() -> None:
    human = enrich_human_with_artifacts("файл", [_ref()])  # type=table

    out = await _hydrate([human], _reads({}))

    assert isinstance(out[0].content, str)


@pytest.mark.asyncio
async def test_hydrate_fail_soft_per_artifact() -> None:
    """Oversize, сбой чтения, не-картиночное расширение — скип блока, текст на месте."""
    human = enrich_human_with_artifacts(
        "фото",
        [
            _image_ref("id-ok"),
            _image_ref("id-fat"),
            _image_ref("id-missing"),
            _image_ref("id-weird", "scan.heic"),
        ],
    )

    out = await _hydrate(
        [human],
        _reads({"id-ok": b"ok", "id-fat": b"x" * 2000}),
        max_bytes=1000,
    )

    content = out[0].content
    assert isinstance(content, list)
    image_blocks = [
        b for b in content if isinstance(b, dict) and b.get("type") == "image"
    ]
    assert len(image_blocks) == 1
    assert image_blocks[0]["base64"] == base64.b64encode(b"ok").decode("ascii")


@pytest.mark.asyncio
async def test_hydrate_all_failed_keeps_message_untouched() -> None:
    human = enrich_human_with_artifacts("фото", [_image_ref("id-gone")])

    out = await _hydrate([human], _reads({}))

    assert isinstance(out[0].content, str)


@pytest.mark.asyncio
async def test_strip_restores_exact_pre_hydration_dump() -> None:
    """Ключевой инвариант: dict-дамп после strip идентичен догидрационному."""
    human = enrich_human_with_artifacts("что на фото?", [_image_ref("id-aaa")])
    lane = [human, AIMessage(content="кот")]
    dump_before = messages_to_dict(lane)

    hydrated = await _hydrate(lane, _reads({"id-aaa": b"bytes"}))
    assert isinstance(hydrated[0].content, list)  # гидрация реально случилась

    assert messages_to_dict(strip_image_blocks(hydrated)) == dump_before


def test_strip_leaves_ai_list_content_untouched() -> None:
    """Списочный контент AI (reasoning-блоки провайдера) — не наш, персистится 1-в-1."""
    ai = AIMessage(content=[{"type": "reasoning", "reasoning": "думаю"}])

    out = strip_image_blocks([ai])

    assert out[0].content == [{"type": "reasoning", "reasoning": "думаю"}]


def test_strip_noop_on_plain_str_human() -> None:
    human = HumanMessage(content="просто текст")

    assert strip_image_blocks([human])[0].content == "просто текст"


def test_strip_restores_first_text_block_only() -> None:
    """Источник правды — блок №0: добавленные text-блоки отбрасываются, не склеиваются."""
    human = HumanMessage(
        content=[
            {"type": "text", "text": "исходный текст"},
            {"type": "image", "base64": "AAA", "mime_type": "image/png"},
            {"type": "text", "text": "служебная пометка"},
        ]
    )

    assert strip_image_blocks([human])[0].content == "исходный текст"


def test_strip_without_text_blocks_gives_empty_str() -> None:
    """Списочный Human без text-блоков схлопывается в пустую строку, не падает."""
    human = HumanMessage(
        content=[{"type": "image", "base64": "AAA", "mime_type": "image/png"}]
    )

    assert strip_image_blocks([human])[0].content == ""
