"""Публикация пакета бурста: один InputEvent на склеенный inbox-пакет.

Внешний критерий — то, что уходит в graph: сколько раз позван publish-контракт,
какой в событии текст, вложения и reply-адрес. Telegram-исходящее (черновик
«думаю») и загрузка файлов подменяются: тест про сборку события, не про сеть.
"""

from contextlib import suppress
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID, uuid4

from aiogram.types import Message
import pytest

from bestfiend.contracts.artifacts import ArtifactRef
from bestfiend.contracts.events import InputEvent
from bestfiend.control_plane.users.models import UserProfile
from bestfiend.telegram.attachment_ingest import AttachmentInfo
from bestfiend.telegram.bot import TelegramBot


_NOW = datetime.now(UTC)
_TEST_BOT_TOKEN = "123456:ABCDEF"
_CHAT_ID = 42
_TELEGRAM_USER_ID = 999


def _message(
    message_id: int,
    *,
    text: str | None = None,
    caption: str | None = None,
    photo_file_id: str | None = None,
    reply_to_message_id: int | None = None,
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
    reply_to = (
        None
        if reply_to_message_id is None
        else SimpleNamespace(message_id=reply_to_message_id)
    )
    fake = SimpleNamespace(
        message_id=message_id,
        chat=SimpleNamespace(id=_CHAT_ID),
        from_user=SimpleNamespace(id=_TELEGRAM_USER_ID),
        message_thread_id=None,
        text=text,
        caption=caption,
        reply_to_message=reply_to,
        photo=photo,
        document=None,
        voice=None,
        audio=None,
        video=None,
        video_note=None,
    )
    return cast(Message, fake)


def _artifact_ref(artifact_id: str = "art-1") -> ArtifactRef:
    return ArtifactRef(
        artifact_id=artifact_id,
        artifact_user_name=f"{artifact_id}.jpg",
        type="image",
        storage_key=f"user/{artifact_id}/data",
    )


class _UserServiceStub:
    """Всегда отдаёт один и тот же активный профиль, считая обращения."""

    def __init__(self, *, status: str = "active") -> None:
        self.profile = UserProfile(
            user_id=uuid4(),
            role="user",
            status=status,  # type: ignore[arg-type]
            telegram_chat_id=_CHAT_ID,
            created_at=_NOW,
        )
        self.calls: list[int] = []

    async def resolve_or_create_by_telegram(
        self,
        telegram_chat_id: int,
    ) -> tuple[UserProfile, bool]:
        self.calls.append(telegram_chat_id)
        return self.profile, False


class _PublishSpy:
    """Publish-контракт core: складывает опубликованные события."""

    def __init__(self) -> None:
        self.events: list[InputEvent] = []

    async def __call__(self, *, event: InputEvent, request_correlation: Any) -> None:
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
    def __init__(self) -> None:
        self.opened: list[str] = []

    def open(self, request_id: str) -> _EmptySubscription:
        self.opened.append(request_id)
        return _EmptySubscription()


class _IngestSpy:
    """Подмена загрузки вложений: запоминает infos и отдаёт заданные артефакты."""

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


class _BotHarness:
    """Собранный бот с подменённым исходящим каналом и загрузкой вложений."""

    def __init__(
        self,
        *,
        uploaded_artifacts: list[ArtifactRef] | None = None,
        allowed_user_ids: list[int] | None = None,
    ) -> None:
        self.users = _UserServiceStub()
        self.publish = _PublishSpy()
        self.outbound_source = _OutboundSourceStub()
        self.ingest = _IngestSpy(uploaded_artifacts or [])
        self.bot = TelegramBot(
            bot_token=_TEST_BOT_TOKEN,
            user_service=cast(Any, self.users),
            publish_input_event=cast(Any, self.publish),
            artifacts=cast(Any, None),
            outbound_source=cast(Any, self.outbound_source),
            attachment_max_size_bytes=10 * 1024 * 1024,
            allowed_user_ids=allowed_user_ids,
        )
        self.bot._ingest.download_and_upload_many = cast(Any, self.ingest)
        self.bot._outbound.send_thinking_draft = cast(Any, _no_thinking_draft)

    @property
    def published(self) -> list[InputEvent]:
        return self.publish.events

    async def publish_bundle(self, bundle: list[Message]) -> None:
        try:
            await self.bot._publish_bundle(bundle)
        finally:
            with suppress(Exception):
                await self.bot.close()

    async def call_content_handler(self, message: Message, **data: Any) -> None:
        """Зовёт зарегистрированный контентный хендлер как это делает aiogram."""
        handler = next(
            registered.callback
            for registered in self.bot.dispatcher.message.handlers
            if registered.callback.__name__ == "on_content"
        )
        try:
            await handler(message, **data)
        finally:
            with suppress(Exception):
                await self.bot.close()


async def _no_thinking_draft(*, chat_id: int, request_id: str) -> None:
    """Черновик «думаю» в тестах не уходит в Telegram."""
    return None


@pytest.mark.asyncio
async def test_text_and_photo_bundle_publishes_single_event() -> None:
    """Фото с подписью в одном бурсте — один InputEvent с текстом и артефактами."""
    harness = _BotHarness(uploaded_artifacts=[_artifact_ref()])
    bundle = [
        _message(1, caption="что на фото?", photo_file_id="photo-1"),
        _message(2, text="и подпиши покороче"),
    ]

    await harness.publish_bundle(bundle)

    assert len(harness.published) == 1
    event = harness.published[0]
    assert event.message == "что на фото?\n\nи подпиши покороче"
    assert [ref.artifact_id for ref in event.attached_artifacts] == ["art-1"]
    assert [info.file_id for info in harness.ingest.calls[0]] == ["photo-1"]


@pytest.mark.asyncio
async def test_text_only_bundle_joins_messages_in_order() -> None:
    """Два текстовых сообщения бурста склеиваются в одно событие через пустую строку."""
    harness = _BotHarness()
    bundle = [_message(10, text="первое"), _message(11, text="второе")]

    await harness.publish_bundle(bundle)

    assert len(harness.published) == 1
    assert harness.published[0].message == "первое\n\nвторое"
    assert harness.published[0].attached_artifacts == []
    assert harness.ingest.calls == []


@pytest.mark.asyncio
async def test_bundle_without_text_and_with_failed_upload_publishes_nothing() -> None:
    """Все файлы упали и текста нет — публиковать нечего, событие не уходит."""
    harness = _BotHarness(uploaded_artifacts=[])
    bundle = [_message(1, photo_file_id="photo-1")]

    await harness.publish_bundle(bundle)

    assert harness.published == []
    assert harness.outbound_source.opened == []


@pytest.mark.asyncio
async def test_anchor_reply_goes_into_event_metadata() -> None:
    """Reply анкера становится reply-адресом всего пакета."""
    harness = _BotHarness()
    bundle = [
        _message(5, text="уточнение", reply_to_message_id=3),
        _message(6, text="ещё строка"),
    ]

    await harness.publish_bundle(bundle)

    metadata = harness.published[0].metadata
    assert metadata["reply_to_message_id"] == 3
    assert metadata["message_id"] == 5
    assert metadata["chat_id"] == _CHAT_ID


@pytest.mark.asyncio
async def test_authorize_runs_once_per_bundle() -> None:
    """Пакет из трёх сообщений авторизуется один раз, а не по сообщению."""
    harness = _BotHarness(uploaded_artifacts=[_artifact_ref()])
    bundle = [
        _message(1, caption="раз", photo_file_id="photo-1"),
        _message(2, photo_file_id="photo-2"),
        _message(3, text="три"),
    ]

    await harness.publish_bundle(bundle)

    assert harness.users.calls == [_TELEGRAM_USER_ID]
    assert len(harness.published) == 1


@pytest.mark.asyncio
async def test_content_handler_without_bundle_uses_single_message() -> None:
    """Вызов хендлера без inbox-контекста обрабатывает одно сообщение как пакет."""
    harness = _BotHarness()

    await harness.call_content_handler(_message(7, text="одиночка"))

    assert len(harness.published) == 1
    assert harness.published[0].message == "одиночка"
    assert harness.published[0].metadata["message_id"] == 7


@pytest.mark.asyncio
async def test_content_handler_without_bundle_rechecks_acl() -> None:
    """Вызов хендлера мимо middleware перепроверяет ACL: чужой юзер не публикуется.

    Неизвестная команда не матчится командными фильтрами и доезжает до
    контентного catch-all — на этом пути ACL проверяет сам хендлер.
    """
    harness = _BotHarness(allowed_user_ids=[_TELEGRAM_USER_ID + 1])

    await harness.call_content_handler(_message(8, text="/unknown"))

    assert harness.published == []
    assert harness.users.calls == []
    assert harness.outbound_source.opened == []
