"""Runtime container для встроенного telegram-bot.

Bot держит refs на `UserService`, `GraphRuntime`, `ArtifactService`,
`StreamPublisher`. Стартует/останавливается через `CoreRuntime.start()`/`stop()`.
"""

import asyncio
from dataclasses import dataclass

from loguru import logger

from bestfiend.telegram.bot import TelegramBot


@dataclass(slots=True)
class TelegramRuntime:
    """Собранный telegram-runtime: bot + lifecycle handle."""

    bot: TelegramBot
    _polling_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Запускает Telegram polling в фоне."""
        if self._polling_task is not None:
            return
        self._polling_task = asyncio.create_task(
            self.bot.start_polling(),
            name="telegram-polling",
        )
        logger.info("telegram: polling task started")

    async def stop(self) -> None:
        """Останавливает polling и закрывает aiogram session."""
        if self._polling_task is not None:
            self._polling_task.cancel()
            try:
                await self._polling_task
            except asyncio.CancelledError:
                logger.debug("telegram: polling task cancelled")
            self._polling_task = None
        await self.bot.close()
        logger.info("telegram: runtime stopped")
