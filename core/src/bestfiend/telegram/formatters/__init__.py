"""Форматирование исходящих сообщений Telegram.

Пакет собирает markdown для нативных Rich Messages (Telegram рендерит GFM сам),
плоский текст для fallback-пути и прогресс-лог, а также режет текст под лимиты
Telegram — по UTF-16 единицам для plain и по символам для rich.

Пример использования:
    from bestfiend.telegram.formatters import compose_final_markdown

    markdown = compose_final_markdown(progress_lines=lines, content=answer)
"""

from bestfiend.telegram.formatters.message_composer import (
    THINKING_DRAFT_MARKDOWN,
    compose_final_markdown,
    compose_plain_fallback,
    split_rich_markdown,
)
from bestfiend.telegram.formatters.progress_formatter import format_progress
from bestfiend.telegram.formatters.text_limits import (
    RICH_MESSAGE_CHAR_LIMIT,
    TELEGRAM_TEXT_LIMIT,
    chunk_by_utf16_limit,
    tail_by_utf16_limit,
    utf16_len,
)


__all__ = [
    "RICH_MESSAGE_CHAR_LIMIT",
    "TELEGRAM_TEXT_LIMIT",
    "THINKING_DRAFT_MARKDOWN",
    "chunk_by_utf16_limit",
    "compose_final_markdown",
    "compose_plain_fallback",
    "format_progress",
    "split_rich_markdown",
    "tail_by_utf16_limit",
    "utf16_len",
]
