"""Telegram `/web` command — in-process AuthService.generate_binding_code."""

from contextlib import suppress
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID, uuid4

import pytest

from bestfiend.control_plane.auth.errors import UserStatusError
from bestfiend.control_plane.auth.models import BindingCodeRecord
from bestfiend.control_plane.users.models import UserProfile
from bestfiend.telegram.bot import TelegramBot


_NOW = datetime.now(UTC)
_TEST_BOT_TOKEN = "123456:ABCDEF"


def _profile(
    user_id: UUID | None = None,
    *,
    status: str = "active",
) -> UserProfile:
    return UserProfile(
        user_id=user_id or uuid4(),
        role="user",
        status=status,  # type: ignore[arg-type]
        telegram_chat_id=999,
        created_at=_NOW,
    )


class _UserServiceStub:
    def __init__(self, profile: UserProfile) -> None:
        self._profile = profile
        self.calls: list[int] = []

    async def resolve_or_create_by_telegram(
        self,
        telegram_chat_id: int,
    ) -> tuple[UserProfile, bool]:
        self.calls.append(telegram_chat_id)
        return self._profile, False


class _AuthServiceStub:
    def __init__(
        self,
        *,
        code: str = "654321",
        raise_status_error: bool = False,
    ) -> None:
        self.calls: list[UUID] = []
        self._code = code
        self._raise = raise_status_error

    async def generate_binding_code(self, user_id: UUID) -> BindingCodeRecord:
        self.calls.append(user_id)
        if self._raise:
            raise UserStatusError("not active")
        return BindingCodeRecord(
            code=self._code,
            user_id=user_id,
            expires_at=_NOW + timedelta(minutes=10),
            created_at=_NOW,
        )


class _FakeFromUser:
    def __init__(self, user_id: int) -> None:
        self.id = user_id


class _FakeChat:
    def __init__(self, chat_id: int) -> None:
        self.id = chat_id


class _FakeMessage:
    def __init__(self, telegram_user_id: int = 999, chat_id: int = 42) -> None:
        self.from_user = _FakeFromUser(telegram_user_id)
        self.chat = _FakeChat(chat_id)
        self.sent: list[str] = []

    async def answer(self, text: str) -> None:
        self.sent.append(text)


def _make_bot(
    user_service: _UserServiceStub,
    *,
    auth_service: _AuthServiceStub | None = None,
) -> TelegramBot:
    return TelegramBot(
        bot_token=_TEST_BOT_TOKEN,
        user_service=cast(Any, user_service),
        publish_input_event=cast(Any, None),
        artifacts=cast(Any, None),
        outbound_source=cast(Any, None),
        attachment_max_size_bytes=10 * 1024 * 1024,
        auth_service=cast(Any, auth_service),
        binding_code_ttl_s=600,
    )


@pytest.mark.asyncio
async def test_web_active_user_returns_code() -> None:
    """Active user + auth_service — bot шлёт код + TTL минут."""
    profile = _profile(status="active")
    users = _UserServiceStub(profile)
    auth = _AuthServiceStub(code="654321")
    bot = _make_bot(users, auth_service=auth)

    msg = _FakeMessage()
    try:
        await bot._handle_web(cast(Any, msg))
    finally:
        with suppress(Exception):
            await bot.close()

    assert len(msg.sent) == 1
    assert "654321" in msg.sent[0]
    assert "10 мин" in msg.sent[0]
    assert auth.calls == [profile.user_id]


@pytest.mark.asyncio
async def test_web_pending_user_rejected_without_calling_auth() -> None:
    """Pending user — отказ, generate_binding_code не вызван."""
    profile = _profile(status="pending")
    users = _UserServiceStub(profile)
    auth = _AuthServiceStub()
    bot = _make_bot(users, auth_service=auth)

    msg = _FakeMessage()
    try:
        await bot._handle_web(cast(Any, msg))
    finally:
        with suppress(Exception):
            await bot.close()

    assert len(msg.sent) == 1
    assert "не активирован" in msg.sent[0]
    assert auth.calls == []


@pytest.mark.asyncio
async def test_web_no_auth_service_graceful() -> None:
    """Ctor без auth_service — graceful skip."""
    users = _UserServiceStub(_profile())
    bot = _make_bot(users, auth_service=None)

    msg = _FakeMessage()
    try:
        await bot._handle_web(cast(Any, msg))
    finally:
        with suppress(Exception):
            await bot.close()

    assert len(msg.sent) == 1
    assert "временно недоступен" in msg.sent[0]


@pytest.mark.asyncio
async def test_web_auth_user_status_error_returns_pending_text() -> None:
    """Если AuthService.generate_binding_code поднимает UserStatusError — pending text."""
    profile = _profile(status="active")
    users = _UserServiceStub(profile)
    auth = _AuthServiceStub(raise_status_error=True)
    bot = _make_bot(users, auth_service=auth)

    msg = _FakeMessage()
    try:
        await bot._handle_web(cast(Any, msg))
    finally:
        with suppress(Exception):
            await bot.close()

    assert "не активирован" in msg.sent[0]


@pytest.mark.asyncio
async def test_web_disallowed_user_no_response() -> None:
    """User вне allowed_user_ids — handler ничего не отвечает."""
    profile = _profile(status="active")
    users = _UserServiceStub(profile)
    bot = TelegramBot(
        bot_token=_TEST_BOT_TOKEN,
        user_service=cast(Any, users),
        publish_input_event=cast(Any, None),
        artifacts=cast(Any, None),
        outbound_source=cast(Any, None),
        attachment_max_size_bytes=10 * 1024 * 1024,
        allowed_user_ids=[111, 222],  # 999 — НЕ в allowed
        auth_service=cast(Any, _AuthServiceStub()),
    )

    msg = _FakeMessage(telegram_user_id=999)
    try:
        await bot._handle_web(cast(Any, msg))
    finally:
        with suppress(Exception):
            await bot.close()

    assert msg.sent == []
    assert users.calls == []
