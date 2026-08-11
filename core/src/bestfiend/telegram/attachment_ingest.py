"""Приём вложений из Telegram: метаданные, size-гейты, скачивание.

Путей два. Файлы (документ, фото, видео, видеозаметка) проходят двойной
size-гейт — по метаданным Telegram и по фактически скачанным байтам — и уезжают
в artifacts. Голосовые и аудио идут своим путём: гейты те же, но байты
возвращаются вызывающему для распознавания речи, артефакт не создаётся.
Склейка сообщений одного бурста живёт в inbox-агрегаторе — сюда приходят уже
готовые сообщения, по одному или пачкой.
"""

import asyncio
from io import BytesIO
from typing import Literal
from uuid import UUID

from aiogram import Bot
from aiogram.types import Message
from loguru import logger

from bestfiend.artifacts.service import ArtifactService
from bestfiend.contracts.artifacts import ArtifactRef


_ART_SOURCE_TELEGRAM = "gateway_telegram"


class AttachmentInfo:
    """Снимок данных attachment'а для скачивания и upload."""

    __slots__ = ("file_id", "filename", "file_size")

    def __init__(
        self,
        *,
        file_id: str,
        filename: str,
        file_size: int | None,
    ) -> None:
        self.file_id = file_id
        self.filename = filename
        self.file_size = file_size


class AudioAttachmentInfo:
    """Снимок голосового или аудиофайла для скачивания в память."""

    __slots__ = ("kind", "file_id", "filename", "file_size", "duration_s")

    def __init__(
        self,
        *,
        kind: Literal["voice", "audio"],
        file_id: str,
        filename: str,
        file_size: int | None,
        duration_s: int,
    ) -> None:
        self.kind = kind
        self.file_id = file_id
        self.filename = filename
        self.file_size = file_size
        self.duration_s = duration_s


class AttachmentIngestionService:
    """Скачивание вложений Telegram и их загрузка в artifacts."""

    def __init__(
        self,
        *,
        bot: Bot,
        artifacts: ArtifactService,
        max_size_bytes: int,
    ) -> None:
        self._bot = bot
        self._artifacts = artifacts
        self._max_size_bytes = max_size_bytes

    async def download_and_upload_many(
        self,
        *,
        authorized_user_id: UUID,
        infos: list[AttachmentInfo],
        error_chat_id: int,
    ) -> list[ArtifactRef]:
        """Параллельная загрузка нескольких файлов в artifacts."""
        tasks = [
            self.download_and_upload_one(
                authorized_user_id=authorized_user_id,
                info=info,
                error_chat_id=error_chat_id,
            )
            for info in infos
        ]
        results = await asyncio.gather(*tasks, return_exceptions=False)
        return [ref for ref in results if ref is not None]

    async def download_and_upload_one(
        self,
        *,
        authorized_user_id: UUID,
        info: AttachmentInfo,
        error_chat_id: int,
    ) -> ArtifactRef | None:
        """Single attachment: size check → download → upload. None при ошибке."""
        payload_bytes = await self._download_within_size_limit(
            info=info,
            error_chat_id=error_chat_id,
        )
        if payload_bytes is None:
            return None

        try:
            return await self._artifacts.create_from_raw(
                user_id=authorized_user_id,
                art_source=_ART_SOURCE_TELEGRAM,
                filename=info.filename,
                payload=payload_bytes,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "telegram: artifacts upload failed file_id={}: {}",
                info.file_id,
                exc,
            )
            await self._bot.send_message(
                chat_id=error_chat_id,
                text=(f"Не удалось обработать файл «{info.filename}», попробуй позже."),
            )
            return None

    async def download_bytes(
        self,
        *,
        info: AudioAttachmentInfo,
        error_chat_id: int,
    ) -> bytes | None:
        """Отдаёт байты голосового или аудио вызывающему. None при ошибке.

        Артефакт не создаётся: содержимое нужно только для распознавания речи.
        """
        return await self._download_within_size_limit(
            info=info,
            error_chat_id=error_chat_id,
        )

    async def _download_within_size_limit(
        self,
        *,
        info: AttachmentInfo | AudioAttachmentInfo,
        error_chat_id: int,
    ) -> bytes | None:
        """Качает файл с проверкой размера до и после загрузки. None при отказе."""
        if info.file_size is not None and info.file_size > self._max_size_bytes:
            await self._report_size_limit(
                filename=info.filename,
                error_chat_id=error_chat_id,
            )
            return None

        try:
            file = await self._bot.get_file(info.file_id)
            if file.file_path is None:
                logger.warning(
                    "telegram: get_file returned no file_path file_id={}",
                    info.file_id,
                )
                await self._bot.send_message(
                    chat_id=error_chat_id,
                    text=(
                        f"Не удалось скачать файл «{info.filename}»: "
                        "Telegram не выдал путь к файлу."
                    ),
                )
                return None
            buffer = BytesIO()
            await self._bot.download_file(file.file_path, destination=buffer)
            payload_bytes = buffer.getvalue()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "telegram: download failed file_id={}: {}",
                info.file_id,
                exc,
            )
            await self._bot.send_message(
                chat_id=error_chat_id,
                text=f"Не удалось скачать файл «{info.filename}», попробуй позже.",
            )
            return None

        # Повторная проверка на случай если Telegram file_size был неточным.
        if len(payload_bytes) > self._max_size_bytes:
            logger.warning(
                "telegram: downloaded payload exceeds limit file_id={} size={}",
                info.file_id,
                len(payload_bytes),
            )
            await self._report_size_limit(
                filename=info.filename,
                error_chat_id=error_chat_id,
            )
            return None

        return payload_bytes

    async def _report_size_limit(self, *, filename: str, error_chat_id: int) -> None:
        """Сообщает юзеру, что файл не проходит по размеру."""
        limit_mb = self._max_size_bytes // (1024 * 1024)
        await self._bot.send_message(
            chat_id=error_chat_id,
            text=f"Файл «{filename}» слишком большой (макс {limit_mb}MB).",
        )


def extract_attachment_info(message: Message) -> AttachmentInfo | None:
    """Достаёт file_id, filename, file_size из файлового attachment'а.

    Голосовые и аудио сюда не попадают — у них свой путь, extract_audio_info.
    """
    extractors = (
        _document_info,
        _photo_info,
        _video_info,
        _video_note_info,
    )
    for extractor in extractors:
        info = extractor(message)
        if info is not None:
            return info
    return None


def extract_audio_info(message: Message) -> AudioAttachmentInfo | None:
    """Достаёт метаданные голосового или аудиофайла из сообщения."""
    if message.voice is not None:
        voice = message.voice
        return AudioAttachmentInfo(
            kind="voice",
            file_id=voice.file_id,
            filename=f"telegram-voice-{voice.file_unique_id}.ogg",
            file_size=voice.file_size,
            duration_s=voice.duration or 0,
        )
    if message.audio is not None:
        audio = message.audio
        return AudioAttachmentInfo(
            kind="audio",
            file_id=audio.file_id,
            filename=audio.file_name or f"telegram-audio-{audio.file_unique_id}.mp3",
            file_size=audio.file_size,
            duration_s=audio.duration or 0,
        )
    return None


def _document_info(message: Message) -> AttachmentInfo | None:
    """Извлекает метаданные документа."""
    if message.document is not None:
        doc = message.document
        return AttachmentInfo(
            file_id=doc.file_id,
            filename=doc.file_name or f"telegram-document-{doc.file_id}",
            file_size=doc.file_size,
        )
    return None


def _photo_info(message: Message) -> AttachmentInfo | None:
    """Извлекает метаданные крупнейшего варианта фотографии."""
    if message.photo:
        largest = max(message.photo, key=lambda p: p.file_size or 0)
        return AttachmentInfo(
            file_id=largest.file_id,
            filename=f"telegram-photo-{largest.file_unique_id}.jpg",
            file_size=largest.file_size,
        )
    return None


def _video_info(message: Message) -> AttachmentInfo | None:
    """Извлекает метаданные видеофайла."""
    if message.video is not None:
        video = message.video
        return AttachmentInfo(
            file_id=video.file_id,
            filename=video.file_name or f"telegram-video-{video.file_unique_id}.mp4",
            file_size=video.file_size,
        )
    return None


def _video_note_info(message: Message) -> AttachmentInfo | None:
    """Извлекает метаданные видеозаметки."""
    if message.video_note is not None:
        note = message.video_note
        return AttachmentInfo(
            file_id=note.file_id,
            filename=f"telegram-video-note-{note.file_unique_id}.mp4",
            file_size=note.file_size,
        )
    return None
