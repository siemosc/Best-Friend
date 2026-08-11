"""Настройки telegram-bot для core monolith."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class TelegramBotSettings(BaseSettings):
    """Runtime-настройки Telegram polling + user upload ограничения."""

    telegram_bot_token: str = ""
    telegram_allowed_user_ids: str = ""
    attachment_max_size_bytes: int = 25 * 1024 * 1024
    # Окно склейки бурста входящих сообщений (альбом, forward + комментарий).
    telegram_inbox_debounce_s: float = 0.5

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )
