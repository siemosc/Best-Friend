"""Троттлинг обновлений Telegram-черновика для стрима ответа.

LLM отдаёт дельты десятками в секунду, а черновик — обычный Bot API вызов:
слать его на каждую дельту нельзя. Коалесер пропускает одну отправку в окно
и запоминает свежий снапшот вместо неё.

Троттлинг сделан без фоновых задач и таймеров: отложенный снапшот отправляет
следующий `submit`, попавший в окно, а не таймер. Поэтому у коалесера нет
lifecycle — нечего останавливать и нечему протекать. Плата за это — последний
снапшот стрима может не уехать в черновик; это безопасно: черновик эфемерен и
гаснет финальным сообщением, которое приходит всегда.
"""

from collections.abc import Awaitable, Callable
from time import monotonic

from aiogram.exceptions import TelegramAPIError
from loguru import logger


# Консервативное окно: рейт-лимит черновиков Telegram не документирован.
_DEFAULT_MIN_INTERVAL_S = 1.0


class DraftStreamCoalescer:
    """Пропускает в черновик не больше одной отправки снапшота за окно времени."""

    def __init__(
        self,
        *,
        send: Callable[[str], Awaitable[None]],
        min_interval_s: float = _DEFAULT_MIN_INTERVAL_S,
    ) -> None:
        self._send = send
        self._min_interval_s = min_interval_s
        self._pending_content: str | None = None
        self._last_sent_at: float | None = None

    async def submit(self, content: str) -> None:
        """Отправляет снапшот стрима, если окно троттлинга истекло; иначе откладывает."""
        self._pending_content = content
        now = monotonic()
        if (
            self._last_sent_at is not None
            and now - self._last_sent_at < self._min_interval_s
        ):
            return

        snapshot = self._pending_content
        self._pending_content = None
        self._last_sent_at = now
        try:
            await self._send(snapshot)
        except TelegramAPIError as exc:
            # Сбой черновика не должен валить потребителя стрима и граф.
            logger.warning("telegram: обновление черновика провалено: {}", exc)

    def reset(self) -> None:
        """Забывает отложенный снапшот и метку последней отправки."""
        self._pending_content = None
        self._last_sent_at = None
