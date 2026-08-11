"""Telegram capability: aiogram bot встроенный в ASGI core.

Bot стартует через `app.lifespan` (см. `bestfiend.app.runtime.CoreRuntime`),
in-process зовёт `UserService` для identity и `GraphRuntime` для обработки
событий — без HTTP-клиентов к своему же стеку.
"""

from bestfiend.telegram.allowed_users import parse_allowed_user_ids
from bestfiend.telegram.bot import TelegramBot
from bestfiend.telegram.runtime import TelegramRuntime
from bestfiend.telegram.settings import TelegramBotSettings


__all__ = [
    "TelegramBot",
    "TelegramBotSettings",
    "TelegramRuntime",
    "parse_allowed_user_ids",
]
