"""Приём вложений Telegram: разбор метаданных и size-гейты скачивания.

Внешний критерий — контракт Telegram Bot API: какие поля сообщения дают файл,
голосовое и аудио, и что вложение сверх лимита до сети не доходит. Сам Telegram
и artifacts подменены: тест про разбор и гейты, не про сеть.
"""

from io import BytesIO
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from aiogram.types import Message
import pytest

from bestfiend.contracts.artifacts import ArtifactRef
from bestfiend.telegram.attachment_ingest import (
    AttachmentIngestionService,
    AudioAttachmentInfo,
    extract_attachment_info,
    extract_audio_info,
)


_MAX_SIZE_BYTES = 2 * 1024 * 1024
_CHAT_ID = 42


def _message(**attachments: Any) -> Message:
    """Фейковое сообщение: все слоты вложений пусты, кроме переданных."""
    fields: dict[str, Any] = {
        "photo": None,
        "document": None,
        "voice": None,
        "audio": None,
        "video": None,
        "video_note": None,
    }
    fields.update(attachments)
    return cast(Message, SimpleNamespace(**fields))


def _voice(
    *,
    file_id: str = "voice-file-1",
    file_unique_id: str = "uniq-voice",
    duration: int | None = 7,
    file_size: int | None = 1024,
) -> SimpleNamespace:
    return SimpleNamespace(
        file_id=file_id,
        file_unique_id=file_unique_id,
        duration=duration,
        file_size=file_size,
    )


def _audio(
    *,
    file_id: str = "audio-file-1",
    file_unique_id: str = "uniq-audio",
    file_name: str | None = None,
    duration: int = 130,
    file_size: int | None = 4096,
) -> SimpleNamespace:
    return SimpleNamespace(
        file_id=file_id,
        file_unique_id=file_unique_id,
        file_name=file_name,
        duration=duration,
        file_size=file_size,
    )


def _audio_info(*, file_size: int | None = 1024) -> AudioAttachmentInfo:
    return AudioAttachmentInfo(
        kind="voice",
        file_id="voice-file-1",
        filename="telegram-voice-uniq-voice.ogg",
        file_size=file_size,
        duration_s=7,
    )


def _artifact_ref() -> ArtifactRef:
    return ArtifactRef(
        artifact_id="art-1",
        artifact_user_name="telegram-photo-uniq.jpg",
        type="image",
        storage_key="user/art-1/data",
    )


def _service(
    *,
    payload: bytes = b"audio-payload",
) -> tuple[AttachmentIngestionService, MagicMock, MagicMock]:
    """Сервис с подменённым Telegram и artifacts; возвращает (сервис, tg, artifacts)."""

    async def _download(file_path: str, *, destination: BytesIO) -> None:
        destination.write(payload)

    tg = MagicMock()
    tg.get_file = AsyncMock(return_value=SimpleNamespace(file_path="voice/file_1.ogg"))
    tg.download_file = AsyncMock(side_effect=_download)
    tg.send_message = AsyncMock()

    artifacts = MagicMock()
    artifacts.create_from_raw = AsyncMock(return_value=_artifact_ref())

    service = AttachmentIngestionService(
        bot=cast(Any, tg),
        artifacts=cast(Any, artifacts),
        max_size_bytes=_MAX_SIZE_BYTES,
    )
    return service, tg, artifacts


def test_extract_audio_info_voice_builds_ogg_filename() -> None:
    """Голосовое: имя строится из file_unique_id, длительность берётся из Telegram."""
    info = extract_audio_info(_message(voice=_voice()))

    assert info is not None
    assert info.kind == "voice"
    assert info.file_id == "voice-file-1"
    assert info.filename == "telegram-voice-uniq-voice.ogg"
    assert info.file_size == 1024
    assert info.duration_s == 7


def test_extract_audio_info_voice_without_duration_falls_back_to_zero() -> None:
    """Telegram не дал длительность — в снимке 0, а не None."""
    info = extract_audio_info(_message(voice=_voice(duration=None)))

    assert info is not None
    assert info.duration_s == 0


def test_extract_audio_info_audio_prefers_original_file_name() -> None:
    """Аудиофайл с именем отправителя: оно и попадает в снимок."""
    info = extract_audio_info(_message(audio=_audio(file_name="podcast.m4a")))

    assert info is not None
    assert info.kind == "audio"
    assert info.filename == "podcast.m4a"
    assert info.duration_s == 130


def test_extract_audio_info_audio_without_name_builds_mp3_filename() -> None:
    """Аудиофайл без имени отправителя получает синтетическое mp3-имя."""
    info = extract_audio_info(_message(audio=_audio()))

    assert info is not None
    assert info.filename == "telegram-audio-uniq-audio.mp3"


def test_extract_audio_info_ignores_non_audio_message() -> None:
    """В сообщении нет голосового и аудио — аудио-снимка тоже нет."""
    document = SimpleNamespace(file_id="doc-1", file_name="report.pdf", file_size=10)

    assert extract_audio_info(_message(document=document)) is None


def test_extract_attachment_info_skips_voice_and_audio() -> None:
    """Голосовое и аудио файловым путём больше не идут — артефакт не создаётся."""
    assert extract_attachment_info(_message(voice=_voice())) is None
    assert extract_attachment_info(_message(audio=_audio(file_name="song.mp3"))) is None


def test_extract_attachment_info_keeps_document() -> None:
    """Документ разбирается как раньше: file_id, имя отправителя, размер."""
    document = SimpleNamespace(file_id="doc-1", file_name="report.pdf", file_size=2048)

    info = extract_attachment_info(_message(document=document))

    assert info is not None
    assert info.file_id == "doc-1"
    assert info.filename == "report.pdf"
    assert info.file_size == 2048


def test_extract_attachment_info_takes_largest_photo() -> None:
    """Фото разбирается как раньше: берётся самый крупный вариант."""
    photo = [
        SimpleNamespace(file_id="small", file_unique_id="u-small", file_size=100),
        SimpleNamespace(file_id="large", file_unique_id="u-large", file_size=900),
    ]

    info = extract_attachment_info(_message(photo=photo))

    assert info is not None
    assert info.file_id == "large"
    assert info.filename == "telegram-photo-u-large.jpg"


@pytest.mark.asyncio
async def test_download_bytes_rejects_oversized_metadata_before_network() -> None:
    """Размер из метаданных сверх лимита: скачивание не начинается, юзер уведомлён."""
    service, tg, _ = _service()

    payload = await service.download_bytes(
        info=_audio_info(file_size=_MAX_SIZE_BYTES + 1),
        error_chat_id=_CHAT_ID,
    )

    assert payload is None
    tg.get_file.assert_not_awaited()
    tg.download_file.assert_not_awaited()
    tg.send_message.assert_awaited_once()
    assert tg.send_message.call_args.kwargs["chat_id"] == _CHAT_ID


@pytest.mark.asyncio
async def test_download_bytes_rejects_oversized_payload_after_download() -> None:
    """Метаданные соврали про размер — гейт по факту всё равно отбивает файл."""
    service, tg, _ = _service(payload=b"x" * (_MAX_SIZE_BYTES + 1))

    payload = await service.download_bytes(
        info=_audio_info(file_size=None),
        error_chat_id=_CHAT_ID,
    )

    assert payload is None
    tg.download_file.assert_awaited_once()
    tg.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_download_bytes_returns_payload_without_artifact_upload() -> None:
    """Успешное скачивание отдаёт байты вызывающему и не трогает artifacts."""
    service, tg, artifacts = _service(payload=b"ogg-bytes")

    payload = await service.download_bytes(info=_audio_info(), error_chat_id=_CHAT_ID)

    assert payload == b"ogg-bytes"
    artifacts.create_from_raw.assert_not_awaited()
    tg.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_download_bytes_reports_download_failure() -> None:
    """Telegram оборвал скачивание — юзеру уходит сообщение, наверх None."""
    service, tg, _ = _service()
    tg.download_file = AsyncMock(side_effect=RuntimeError("boom"))

    payload = await service.download_bytes(info=_audio_info(), error_chat_id=_CHAT_ID)

    assert payload is None
    tg.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_download_and_upload_one_uploads_downloaded_payload() -> None:
    """Файловый путь не изменился: скачанные байты уезжают в artifacts."""
    service, tg, artifacts = _service(payload=b"file-bytes")
    user_id = uuid4()
    info = extract_attachment_info(
        _message(
            document=SimpleNamespace(
                file_id="doc-1",
                file_name="report.pdf",
                file_size=2048,
            )
        )
    )
    assert info is not None

    ref = await service.download_and_upload_one(
        authorized_user_id=user_id,
        info=info,
        error_chat_id=_CHAT_ID,
    )

    assert ref is not None
    assert ref.artifact_id == "art-1"
    artifacts.create_from_raw.assert_awaited_once()
    upload_kwargs = artifacts.create_from_raw.call_args.kwargs
    assert upload_kwargs["user_id"] == user_id
    assert upload_kwargs["filename"] == "report.pdf"
    assert upload_kwargs["payload"] == b"file-bytes"
    tg.send_message.assert_not_awaited()
