"""Инъекция приложенных юзером артефактов в HumanMessage (ingress) и их гидрация.

Приложенные файлы впечатываются в текст сообщения юзера MD-блоком (имя + описание —
то, что видит модель), а структурные рефы кладутся в `additional_kwargs`. Сообщение
персистится в STM 1-в-1, поэтому артефакт остаётся в истории всех последующих ходов.

Рефы — источник для гидрации: на invoke-time image-рефы разворачиваются в нативные
image-блоки контента (модель видит картинку), перед персистом контент схлопывается
обратно в str (`strip_image_blocks`) — base64 в лог не попадает. Модель без vision
вместо картинок получает текстовую пометку о них (`annotate_unsupported_images`),
такую же volatile: strip снимает и её.

Рендер намеренно НЕ переиспользует egress-функцию из `mcp.coercion`: одинаковый
per-ref дизайн, но развязанные модули и свой заголовок.
"""

import base64
from collections.abc import Awaitable, Callable
from typing import Any

from langchain_core.messages import BaseMessage, HumanMessage
from loguru import logger

from bestfiend.artifacts.service import image_mime_type
from bestfiend.contracts.artifacts import ArtifactRef


ReadImageBytes = Callable[[str], Awaitable[bytes]]
"""Чтение байтов картинки по artifact_id (ключ строит владелец user_id)."""

HydrateImages = Callable[[list[BaseMessage]], Awaitable[list[BaseMessage]]]
"""Pre-bound обработка картинок в ленте; None у держателя = конфиг модели нечитаем."""


_ATTACHED_ARTIFACTS_KEY = "attached_artifacts"
# Whitelist полей рефа в истории. storage_key НЕ храним: ключ детерминированный
# ({user_id}/{artifact_id}/data) — будущий re-delivery-резолвер строит его из session
# user_id, чтобы не доверять персистнутому кросс-юзерному пути. art_meta — без читателя.
_PERSISTED_REF_FIELDS = {"artifact_id", "artifact_user_name", "type", "description"}


def enrich_human_with_artifacts(message: str, refs: list[ArtifactRef]) -> HumanMessage:
    """Собирает HumanMessage: текст юзера + MD-блок файлов, рефы — в additional_kwargs."""
    content = f"{message}\n\n{render_attached_artifacts_md(refs)}"
    return HumanMessage(
        content=content,
        additional_kwargs={
            _ATTACHED_ARTIFACTS_KEY: [
                ref.model_dump(mode="json", include=_PERSISTED_REF_FIELDS)
                for ref in refs
            ]
        },
    )


def render_attached_artifacts_md(refs: list[ArtifactRef]) -> str:
    """MD-блок приложенных файлов (ingress): заголовок + bullets `имя — описание`."""
    if not refs:
        return ""
    bullets = "\n".join(_artifact_bullet(ref) for ref in refs)
    return f"Приложенные файлы:\n{bullets}"


def _artifact_bullet(ref: ArtifactRef) -> str:
    """Один bullet приложенного файла: `artifact_llm_name` + ` — описание`, если оно есть."""
    line = f"- `{ref.artifact_llm_name}`"
    if ref.description:
        line += f" — {ref.description}"
    return line


_IMAGE_ARTIFACT_TYPE = "image"


async def hydrate_image_artifacts(
    messages: list[BaseMessage],
    *,
    read_image: ReadImageBytes,
    max_history_images: int,
    max_image_bytes: int,
) -> list[BaseMessage]:
    """Разворачивает image-рефы Human-сообщений в нативные image-блоки контента.

    Последнее сообщение (текущий ход) гидрируется целиком, без капа. История —
    от хвоста, суммарно ≤ max_history_images картинок: сообщение на границе
    бюджета гидрируется частично (первые N рефов, остальные — текстом), более
    старые не гидрируются вовсе (последние K — непрерывный хвост, без «дыр»).
    Fail-soft per-артефакт: oversize, сбой чтения, не-картиночное расширение →
    реф остаётся только текстом MD-блока.
    """
    hydrated = list(messages)
    history_budget = max_history_images
    for idx in range(len(hydrated) - 1, -1, -1):
        is_current_turn = idx == len(hydrated) - 1
        if not is_current_turn and history_budget <= 0:
            break
        max_images = None if is_current_turn else history_budget
        hydrated[idx], hydrated_count = await _hydrate_message_images(
            hydrated[idx],
            read_image=read_image,
            max_image_bytes=max_image_bytes,
            max_images=max_images,
        )
        if not is_current_turn:
            history_budget -= hydrated_count
    return hydrated


async def _hydrate_message_images(
    message: BaseMessage,
    *,
    read_image: ReadImageBytes,
    max_image_bytes: int,
    max_images: int | None,
) -> tuple[BaseMessage, int]:
    """Гидрирует изображения одного Human-сообщения."""
    if not isinstance(message, HumanMessage) or not isinstance(message.content, str):
        return message, 0
    image_refs = _image_refs(message)
    selected_refs = image_refs if max_images is None else image_refs[:max_images]
    blocks = await _image_blocks(
        selected_refs,
        read_image=read_image,
        max_image_bytes=max_image_bytes,
    )
    if not blocks:
        return message, 0
    hydrated = message.model_copy(
        update={"content": [{"type": "text", "text": message.content}, *blocks]}
    )
    return hydrated, len(blocks)


def _image_refs(message: HumanMessage) -> list[dict[str, Any]]:
    """Извлекает сохранённые рефы изображений из сообщения."""
    refs = message.additional_kwargs.get(_ATTACHED_ARTIFACTS_KEY) or []
    return [
        ref
        for ref in refs
        if isinstance(ref, dict) and ref.get("type") == _IMAGE_ARTIFACT_TYPE
    ]


async def _image_blocks(
    refs: list[dict[str, Any]],
    *,
    read_image: ReadImageBytes,
    max_image_bytes: int,
) -> list[dict[str, Any]]:
    """Читает валидные image-блоки для списка рефов."""
    blocks: list[dict[str, Any]] = []
    for ref in refs:
        block = await _image_block_from_ref(
            ref, read_image=read_image, max_image_bytes=max_image_bytes
        )
        if block is not None:
            blocks.append(block)
    return blocks


async def _image_block_from_ref(
    ref: dict[str, Any],
    *,
    read_image: ReadImageBytes,
    max_image_bytes: int,
) -> dict[str, Any] | None:
    """v1 ImageContentBlock из рефа; None — артефакт не годится (fail-soft)."""
    artifact_id = str(ref.get("artifact_id") or "")
    artifact_user_name = str(ref.get("artifact_user_name") or "")
    mime_type = image_mime_type(artifact_user_name)
    if not artifact_id or mime_type is None:
        logger.warning(
            "attached_artifacts: image-реф без id или mime-типа, скип: {}",
            artifact_user_name,
        )
        return None
    try:
        data = await read_image(artifact_id)
    except Exception as exc:  # noqa: BLE001 — fail-soft: реф остаётся текстом
        logger.warning(
            "attached_artifacts: чтение картинки {} не удалось: {}", artifact_id, exc
        )
        return None
    if len(data) > max_image_bytes:
        logger.warning(
            "attached_artifacts: картинка {} больше лимита ({} > {} байт), скип",
            artifact_user_name,
            len(data),
            max_image_bytes,
        )
        return None
    return {
        "type": "image",
        "base64": base64.b64encode(data).decode("ascii"),
        "mime_type": mime_type,
    }

VISION_FALLBACK_NOTE = (
    "[система: пользователь прислал изображение, но текущая модель не поддерживает "
    "зрение. Скажи пользователю прямо, что не можешь посмотреть картинку, и предложи "
    "описать её текстом.]"
)


async def annotate_unsupported_images(
    messages: list[BaseMessage],
) -> list[BaseMessage]:
    """Помечает текущий ход текстовой заметкой о картинке — фолбэк для модели без vision.

    Аннотируется только последнее сообщение ленты (текущий ход) и только если это
    Human со str-контентом и image-рефами: исторические рефы уже отвечены, пометка
    по ним сбивала бы модель. Заметка volatile — `strip_image_blocks` снимает её
    перед персистом. Async — ради совместимости с сигнатурой `HydrateImages`.
    """
    if not messages:
        return list(messages)
    current_turn = messages[-1]
    if not isinstance(current_turn, HumanMessage):
        return list(messages)
    if not isinstance(current_turn.content, str):
        return list(messages)
    if not _image_refs(current_turn):
        return list(messages)
    annotated = current_turn.model_copy(
        update={
            "content": [
                {"type": "text", "text": current_turn.content},
                {"type": "text", "text": VISION_FALLBACK_NOTE},
            ]
        }
    )
    return [*messages[:-1], annotated]


def strip_image_blocks(messages: list[BaseMessage]) -> list[BaseMessage]:
    """Схлопывает гидрированный Human-контент обратно в str — обратная гидрации.

    Источник правды — первый text-блок: оба гидратора кладут исходный текст блоком
    №0, а всё добавленное (image-блоки, vision-пометка) отбрасывается — дамп
    восстанавливается 1-в-1. Трогает только HumanMessage со списочным контентом:
    списочный контент других типов сообщений (reasoning-блоки AI и т.п.) —
    провайдерский, персистится 1-в-1.
    """
    stripped = list(messages)
    for idx, message in enumerate(stripped):
        if not isinstance(message, HumanMessage):
            continue
        if not isinstance(message.content, list):
            continue
        original_text = next(
            (
                block.get("text", "")
                for block in message.content
                if isinstance(block, dict) and block.get("type") == "text"
            ),
            "",
        )
        stripped[idx] = message.model_copy(update={"content": original_text})
    return stripped
