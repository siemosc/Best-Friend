"""Отдача артефактов юзеру в Telegram: раздельные альбомы по типам, fail-soft.

Фото и документы — раздельные альбомы (Telegram не миксует типы в одной
media group), сначала фото, затем документы. Группа из 1 файла — обычным
send (альбом требует ≥2), из ≥2 — чанками по ≤10.
"""

from typing import Literal

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import (
    BufferedInputFile,
    InputMediaDocument,
    InputMediaPhoto,
    MediaUnion,
)
from loguru import logger

from bestfiend.artifacts.service import ArtifactService
from bestfiend.contracts.artifacts import ArtifactRef


# Telegram media group: максимум элементов в одном альбоме (sendMediaGroup, 2-10).
_MEDIA_GROUP_MAX_ITEMS = 10


class ArtifactDelivery:
    """Качает артефакты по storage_key и шлёт их юзеру альбомами."""

    def __init__(self, *, bot: Bot, artifacts: ArtifactService) -> None:
        self._bot = bot
        self._artifacts = artifacts

    async def send_attachments(
        self,
        *,
        chat_id: int,
        request_id: str,
        attachments: list[ArtifactRef],
    ) -> None:
        """Отдаёт артефакты альбомом: качает по storage_key, группирует по типу.

        Сбой скачивания или отправки одного не рушит остальные.
        """
        downloaded = await self._download_attachments(request_id, attachments)
        if not downloaded:
            return
        photos = [(ref, data) for ref, data in downloaded if ref.type == "image"]
        docs = [(ref, data) for ref, data in downloaded if ref.type != "image"]
        await self._send_artifact_group(chat_id, request_id, photos, kind="photo")
        await self._send_artifact_group(chat_id, request_id, docs, kind="document")

    async def _download_attachments(
        self, request_id: str, attachments: list[ArtifactRef]
    ) -> list[tuple[ArtifactRef, bytes]]:
        """Качает байты артефактов по storage_key; сбой одного — skip (fail-soft)."""
        result: list[tuple[ArtifactRef, bytes]] = []
        for ref in attachments:
            try:
                data = await self._artifacts.read_bytes(ref.storage_key)
            except Exception as exc:  # noqa: BLE001 — сбой одного не рушит остальные
                logger.warning(
                    "telegram: artifact download failed request_id={} key={}: {}",
                    request_id,
                    ref.storage_key,
                    exc,
                )
                continue
            result.append((ref, data))
        return result

    async def _send_artifact_group(
        self,
        chat_id: int,
        request_id: str,
        items: list[tuple[ArtifactRef, bytes]],
        *,
        kind: Literal["photo", "document"],
    ) -> None:
        """Шлёт группу одного типа: 1 файл — обычным send, ≥2 — альбомами по ≤10."""
        if not items:
            return
        if len(items) == 1:
            ref, data = items[0]
            await self._send_single_artifact(chat_id, request_id, ref, data, kind=kind)
            return
        for start in range(0, len(items), _MEDIA_GROUP_MAX_ITEMS):
            chunk = items[start : start + _MEDIA_GROUP_MAX_ITEMS]
            if len(chunk) == 1:
                # Остаток-в-1: media group требует ≥2, шлём обычным send.
                ref, data = chunk[0]
                await self._send_single_artifact(
                    chat_id, request_id, ref, data, kind=kind
                )
                continue
            media: list[MediaUnion] = [
                _build_input_media(kind, data, ref.artifact_user_name)
                for ref, data in chunk
            ]
            try:
                await self._bot.send_media_group(chat_id=chat_id, media=media)
            except TelegramAPIError as exc:
                logger.warning(
                    "telegram: media group send failed request_id={} kind={}: {}",
                    request_id,
                    kind,
                    exc,
                )

    async def _send_single_artifact(
        self,
        chat_id: int,
        request_id: str,
        ref: ArtifactRef,
        data: bytes,
        *,
        kind: Literal["photo", "document"],
    ) -> None:
        """Один артефакт обычным send_photo/send_document (fail-soft)."""
        file = BufferedInputFile(data, filename=ref.artifact_user_name)
        try:
            if kind == "photo":
                await self._bot.send_photo(chat_id=chat_id, photo=file)
            else:
                await self._bot.send_document(chat_id=chat_id, document=file)
        except TelegramAPIError as exc:
            logger.warning(
                "telegram: attachment send failed request_id={} name={}: {}",
                request_id,
                ref.artifact_user_name,
                exc,
            )


def _build_input_media(
    kind: Literal["photo", "document"], data: bytes, filename: str
) -> InputMediaPhoto | InputMediaDocument:
    """Строит InputMedia* для media group по типу группы."""
    file = BufferedInputFile(data, filename=filename)
    if kind == "photo":
        return InputMediaPhoto(media=file)
    return InputMediaDocument(media=file)
