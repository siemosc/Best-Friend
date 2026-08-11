"""Голосовые и аудио пакета: транскрипт в событии, отказы — сообщением юзеру.

Внешний критерий — контракт этапа STT: что уезжает в graph (текст события и
вложения), когда публикации нет вовсе и что юзер получает вместо расшифровки.
Telegram, скачивание файлов и сам распознаватель подменены: тест про сборку
пакета, не про сеть.
"""

from contextlib import suppress
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

from aiogram.types import Message
import pytest

from bestfiend.contracts.artifacts import ArtifactRef
from bestfiend.contracts.events import InputEvent
from bestfiend.control_plane.users.models import UserProfile
from bestfiend.telegram.attachment_ingest import AttachmentInfo, AudioAttachmentInfo
from bestfiend.telegram.bot import TelegramBot


_NOW = datetime.now(UTC)
_TEST_BOT_TOKEN = "123456:ABCDEF"
_CHAT_ID = 42
_TELEGRAM_USER_ID = 999
_MAX_DURATION_S = 300
_AUDIO_PAYLOAD = b"ogg-bytes"


def _message(
    message_id: int,
    *,
    text: str | None = None,
    caption: str | None = None,
    voice: SimpleNamespace | None = None,
    audio: SimpleNamespace | None = None,
    photo_file_id: str | None = None,
) -> Message:
    """Фейковое сообщение Telegram: только поля, которые читает контентный путь."""
    photo = (
        None
        if photo_file_id is None
        else [
            SimpleNamespace(
                file_id=photo_file_id,
                file_unique_id=f"unique-{photo_file_id}",
                file_size=1024,
            )
        ]
    )
    fake = SimpleNamespace(
        message_id=message_id,
        chat=SimpleNamespace(id=_CHAT_ID),
        from_user=SimpleNamespace(id=_TELEGRAM_USER_ID),
        message_thread_id=None,
        text=text,
        caption=caption,
        reply_to_message=None,
        photo=photo,
        document=None,
        voice=voice,
        audio=audio,
        video=None,
        video_note=None,
    )
    return cast(Message, fake)


def _voice(*, duration: int = 7, file_id: str = "voice-1") -> SimpleNamespace:
    return SimpleNamespace(
        file_id=file_id,
        file_unique_id=f"uniq-{file_id}",
        duration=duration,
        file_size=1024,
    )


def _audio(
    *,
    file_name: str | None = "podcast.mp3",
    duration: int = 120,
) -> SimpleNamespace:
    return SimpleNamespace(
        file_id="audio-1",
        file_unique_id="uniq-audio-1",
        file_name=file_name,
        duration=duration,
        file_size=4096,
    )


def _artifact_ref(artifact_id: str = "art-1") -> ArtifactRef:
    return ArtifactRef(
        artifact_id=artifact_id,
        artifact_user_name=f"{artifact_id}.jpg",
        type="image",
        storage_key=f"user/{artifact_id}/data",
    )


class _UserServiceStub:
    """Всегда отдаёт один и тот же активный профиль."""

    def __init__(self) -> None:
        self.profile = UserProfile(
            user_id=uuid4(),
            role="user",
            status="active",
            telegram_chat_id=_CHAT_ID,
            created_at=_NOW,
        )

    async def resolve_or_create_by_telegram(
        self,
        telegram_chat_id: int,
    ) -> tuple[UserProfile, bool]:
        return self.profile, False


class _PublishSpy:
    """Publish-контракт core: складывает опубликованные события в общий таймлайн."""

    def __init__(self, timeline: list[str]) -> None:
        self.events: list[InputEvent] = []
        self._timeline = timeline

    async def __call__(self, *, event: InputEvent, request_correlation: Any) -> None:
        self._timeline.append("publish")
        self.events.append(event)


class _EmptySubscription:
    """Подписка без событий: стрим закрывается сразу после старта graph-таска."""

    def __aiter__(self) -> "_EmptySubscription":
        return self

    async def __anext__(self) -> Any:
        raise StopAsyncIteration

    async def close(self) -> None:
        return None


class _OutboundSourceStub:
    def open(self, request_id: str) -> _EmptySubscription:
        return _EmptySubscription()


class _UploadSpy:
    """Подмена загрузки вложений в artifacts: запоминает infos, отдаёт артефакты."""

    def __init__(self, artifacts: list[ArtifactRef]) -> None:
        self.artifacts = artifacts
        self.calls: list[list[AttachmentInfo]] = []

    async def __call__(
        self,
        *,
        authorized_user_id: UUID,
        infos: list[AttachmentInfo],
        error_chat_id: int,
    ) -> list[ArtifactRef]:
        self.calls.append(infos)
        return self.artifacts


class _DownloadBytesSpy:
    """Подмена скачивания аудио в память: запоминает снимки, отдаёт заданные байты."""

    def __init__(self, payload: bytes | None, timeline: list[str]) -> None:
        self.payload = payload
        self.calls: list[AudioAttachmentInfo] = []
        self._timeline = timeline

    async def __call__(
        self,
        *,
        info: AudioAttachmentInfo,
        error_chat_id: int,
    ) -> bytes | None:
        self._timeline.append("download")
        self.calls.append(info)
        return self.payload


class _TranscriberStub:
    """Распознаватель речи с заранее заданным исходом."""

    def __init__(self, transcript: str | None, timeline: list[str]) -> None:
        self.transcript = transcript
        self.calls: list[tuple[bytes, str]] = []
        self._timeline = timeline

    async def transcribe(self, audio: bytes, filename: str) -> str | None:
        self._timeline.append("transcribe")
        self.calls.append((audio, filename))
        return self.transcript


class _ThinkingDraftSpy:
    """Черновик «думаю»: в Telegram не уходит, попадает в таймлайн."""

    def __init__(self, timeline: list[str]) -> None:
        self.calls: list[str] = []
        self._timeline = timeline

    async def __call__(self, *, chat_id: int, request_id: str) -> None:
        self._timeline.append("draft")
        self.calls.append(request_id)


class _BotHarness:
    """Бот с подменёнными Telegram-исходящим, скачиванием и распознавателем."""

    def __init__(
        self,
        *,
        transcript: str | None = "привет из голосового",
        with_transcriber: bool = True,
        downloaded_audio: bytes | None = _AUDIO_PAYLOAD,
        uploaded_artifacts: list[ArtifactRef] | None = None,
    ) -> None:
        self.timeline: list[str] = []
        self.users = _UserServiceStub()
        self.publish = _PublishSpy(self.timeline)
        self.upload = _UploadSpy(uploaded_artifacts or [])
        self.download = _DownloadBytesSpy(downloaded_audio, self.timeline)
        self.thinking_draft = _ThinkingDraftSpy(self.timeline)
        self.transcriber = (
            _TranscriberStub(transcript, self.timeline) if with_transcriber else None
        )
        self.bot = TelegramBot(
            bot_token=_TEST_BOT_TOKEN,
            user_service=cast(Any, self.users),
            publish_input_event=cast(Any, self.publish),
            artifacts=cast(Any, None),
            outbound_source=cast(Any, _OutboundSourceStub()),
            attachment_max_size_bytes=10 * 1024 * 1024,
            transcriber=cast(Any, self.transcriber),
            stt_max_duration_s=_MAX_DURATION_S,
        )
        self.bot._ingest.download_and_upload_many = cast(Any, self.upload)
        self.bot._ingest.download_bytes = cast(Any, self.download)
        self.bot._outbound.send_thinking_draft = cast(Any, self.thinking_draft)
        self.send_message = AsyncMock()
        self.bot.bot.send_message = self.send_message

    @property
    def published(self) -> list[InputEvent]:
        return self.publish.events

    @property
    def notices(self) -> list[str]:
        """Тексты служебных сообщений, ушедших юзеру."""
        return [call.kwargs["text"] for call in self.send_message.call_args_list]

    async def publish_bundle(self, bundle: list[Message]) -> None:
        try:
            await self.bot._publish_bundle(bundle)
        finally:
            with suppress(Exception):
                await self.bot.close()


@pytest.mark.asyncio
async def test_voice_transcript_goes_into_event_text() -> None:
    """Голосовое распознано — транскрипт уезжает текстом, артефакт не создаётся."""
    harness = _BotHarness(transcript="купи молока")

    await harness.publish_bundle([_message(1, voice=_voice())])

    assert len(harness.published) == 1
    event = harness.published[0]
    assert event.message == "[транскрипция голосового: «купи молока»]"
    assert event.attached_artifacts == []
    assert harness.upload.calls == []
    assert harness.notices == []


@pytest.mark.asyncio
async def test_audio_transcript_marker_carries_filename() -> None:
    """Аудиофайл распознан — в маркере остаётся имя файла, чтобы был виден источник."""
    harness = _BotHarness(transcript="лекция про кофе")

    await harness.publish_bundle([_message(1, audio=_audio(file_name="lecture.mp3"))])

    assert harness.published[0].message == (
        "[транскрипция аудио «lecture.mp3»: «лекция про кофе»]"
    )


@pytest.mark.asyncio
async def test_failed_transcription_keeps_bundle_text_and_notifies() -> None:
    """Распознавание отказало, но текст в пакете есть — событие уходит без маркера."""
    harness = _BotHarness(transcript=None)
    bundle = [_message(1, text="а вот ещё"), _message(2, voice=_voice())]

    await harness.publish_bundle(bundle)

    assert len(harness.published) == 1
    assert harness.published[0].message == "а вот ещё"
    assert harness.notices == [
        "Не разобрал голосовое — попробуй ещё раз или напиши текстом."
    ]


@pytest.mark.asyncio
async def test_voice_caption_survives_failed_transcription() -> None:
    """Подпись к голосовому сохраняется даже при отказе распознавания."""
    harness = _BotHarness(transcript="")
    bundle = [_message(1, caption="слушай сюда", voice=_voice())]

    await harness.publish_bundle(bundle)

    assert harness.published[0].message == "слушай сюда"
    assert "транскрипция" not in harness.published[0].message
    assert len(harness.notices) == 1


@pytest.mark.asyncio
async def test_voice_only_bundle_with_failed_transcription_publishes_nothing() -> None:
    """Голосовое без текста не распозналось — графа не будит, но юзеру отвечает."""
    harness = _BotHarness(transcript=None)

    await harness.publish_bundle([_message(1, voice=_voice())])

    assert harness.published == []
    assert harness.notices == [
        "Не разобрал голосовое — попробуй ещё раз или напиши текстом."
    ]


@pytest.mark.asyncio
async def test_voice_over_duration_limit_is_rejected_before_download() -> None:
    """Запись длиннее лимита отбивается до скачивания, юзер видит длительность и лимит."""
    harness = _BotHarness()

    await harness.publish_bundle(
        [_message(1, voice=_voice(duration=_MAX_DURATION_S + 1))]
    )

    assert harness.download.calls == []
    assert harness.published == []
    assert len(harness.notices) == 1
    notice = harness.notices[0]
    assert str(_MAX_DURATION_S + 1) in notice
    assert str(_MAX_DURATION_S) in notice


@pytest.mark.asyncio
async def test_missing_transcriber_reports_once_per_bundle() -> None:
    """Распознаватель не подключён — одно сообщение на пакет, публикации нет."""
    harness = _BotHarness(with_transcriber=False)
    bundle = [_message(1, voice=_voice()), _message(2, voice=_voice(file_id="voice-2"))]

    await harness.publish_bundle(bundle)

    assert harness.published == []
    assert harness.download.calls == []
    assert harness.notices == ["Голосовые сейчас не обрабатываются — напиши текстом."]


@pytest.mark.asyncio
async def test_failed_download_stays_silent_because_ingest_already_reported() -> None:
    """Файл не доехал — приём вложений уже написал юзеру, бот второй раз не пишет."""
    harness = _BotHarness(downloaded_audio=None)

    await harness.publish_bundle([_message(1, voice=_voice())])

    assert harness.published == []
    assert harness.notices == []
    assert harness.transcriber is not None
    assert harness.transcriber.calls == []


@pytest.mark.asyncio
async def test_early_thinking_draft_precedes_transcription() -> None:
    """Пакет с аудио: «думаю» уходит до скачивания и распознавания."""
    harness = _BotHarness()

    await harness.publish_bundle([_message(1, voice=_voice())])

    assert harness.timeline == ["draft", "download", "transcribe", "draft", "publish"]


@pytest.mark.asyncio
async def test_text_only_bundle_has_no_early_thinking_draft() -> None:
    """Чисто текстовый пакет ждать нечего — черновик уходит только на публикации."""
    harness = _BotHarness()

    await harness.publish_bundle([_message(1, text="просто текст")])

    assert harness.timeline == ["draft", "publish"]


@pytest.mark.asyncio
async def test_photo_with_voice_splits_into_artifact_and_transcript() -> None:
    """Фото и голосовое в одном бурсте: фото — артефактом, голосовое — текстом."""
    harness = _BotHarness(
        transcript="что на фото",
        uploaded_artifacts=[_artifact_ref()],
    )
    bundle = [_message(1, photo_file_id="photo-1"), _message(2, voice=_voice())]

    await harness.publish_bundle(bundle)

    event = harness.published[0]
    assert event.message == "[транскрипция голосового: «что на фото»]"
    assert [ref.artifact_id for ref in event.attached_artifacts] == ["art-1"]
    assert [info.file_id for info in harness.upload.calls[0]] == ["photo-1"]
