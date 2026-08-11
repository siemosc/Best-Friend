"""Склейка бурста входящих Telegram-сообщений в один пакет.

Telegram технически рвёт одно обращение пользователя на несколько сообщений:
альбом, forward + комментарий следом, «фото и подпись отдельно». Агрегатор
держит скользящее окно на ключ `(chat_id, telegram_user_id, message_thread_id)`:
первый `collect` по ключу владеет окном и сам спит до дедлайна, каждое
следующее сообщение дедлайн продлевает и возвращает `None`. Окном владеет
handler-таск aiogram — detached-таймеров и фоновых тасков здесь нет.
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, cast

from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject
from loguru import logger


# Ключ окна агрегации: (chat_id, telegram_user_id, message_thread_id).
BurstKey = tuple[int, int, int | None]

# Ключ контекста aiogram, под которым handler получает собранный пакет бурста.
INBOX_BUNDLE_KEY = "inbox_bundle"

_COMMAND_PREFIX = "/"


def burst_key(message: Message) -> BurstKey | None:
    """Ключ окна агрегации сообщения; None — сообщение не агрегируется."""
    if message.from_user is None:
        return None
    return (message.chat.id, message.from_user.id, message.message_thread_id)


class InboxAggregator:
    """Собирает сообщения одного ключа в пакет по скользящему окну ожидания."""

    def __init__(self, *, window_s: float) -> None:
        self._window_s = window_s
        self._buffers: dict[BurstKey, list[Message]] = {}
        self._deadlines: dict[BurstKey, float] = {}

    async def collect(self, key: BurstKey, message: Message) -> list[Message] | None:
        """Пакет бурста для владельца окна; None — сообщение ушло в чужой буфер."""
        loop = asyncio.get_running_loop()
        buffer = self._buffers.get(key)
        if buffer is not None:
            # Окном владеет другой таск: докладываем сообщение и продлеваем дедлайн.
            buffer.append(message)
            self._deadlines[key] = loop.time() + self._window_s
            return None

        self._buffers[key] = [message]
        self._deadlines[key] = loop.time() + self._window_s
        try:
            while True:
                remaining = self._deadlines[key] - loop.time()
                if remaining <= 0:
                    break
                await asyncio.sleep(remaining)
            bundle = self._buffers[key]
        finally:
            # Владелец чистит окно и при отмене таска: осиротевший буфер
            # проглотил бы следующий бурст целиком — его никто не заберёт.
            self._buffers.pop(key, None)
            self._deadlines.pop(key, None)
        return sorted(bundle, key=lambda msg: msg.message_id)


class InboxMiddleware(BaseMiddleware):
    """Outer-middleware: единый ACL-гейт входа, контентный путь — пакетом бурста."""

    def __init__(
        self,
        *,
        aggregator: InboxAggregator,
        is_allowed: Callable[[int], bool],
    ) -> None:
        self._aggregator = aggregator
        self._is_allowed = is_allowed

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        """Гейтит доступ, копит контентный бурст и зовёт handler один раз на пакет."""
        message = cast(Message, event)

        # ACL-гейт стоит перед всеми ветками, включая команды: неизвестная
        # команда не матчится командными хендлерами и падает в контентный
        # catch-all, где проверять доступ уже некому.
        key = burst_key(message)
        if key is None:
            logger.info(
                "telegram: inbox drop message without from_user chat_id={} message_id={}",
                message.chat.id,
                message.message_id,
            )
            return None

        telegram_user_id = key[1]
        if not self._is_allowed(telegram_user_id):
            logger.info("telegram: access denied user_id={}", telegram_user_id)
            return None

        text = message.text
        if text is not None and text.startswith(_COMMAND_PREFIX):
            return await handler(event, data)
        if not _carries_content(message):
            return await handler(event, data)

        bundle = await self._aggregator.collect(key, message)
        if bundle is None:
            return None

        # Анкер пакета — сообщение с минимальным message_id: его chat/reply/объект
        # становятся адресом всего бурста.
        anchor = bundle[0]
        data[INBOX_BUNDLE_KEY] = bundle
        return await handler(anchor, data)


def _carries_content(message: Message) -> bool:
    """Сообщение несёт контент для агента: текст или поддерживаемое вложение."""
    return bool(
        message.text
        or message.photo
        or message.document
        or message.voice
        or message.audio
        or message.video
        or message.video_note
    )
