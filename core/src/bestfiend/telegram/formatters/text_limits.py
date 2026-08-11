"""Лимиты длины Telegram-сообщений и нарезка текста по UTF-16 метрике.

Telegram считает длину plain-текста в UTF-16 code units: символ вне BMP
(эмодзи, редкие иероглифы) стоит две единицы. Python считает тот же текст
в code points, поэтому `len()` занижает длину и наивная нарезка ломает
границу сообщения. Нарезка идёт по code points Python — суррогатная пара
внутри одного code point не разрывается по построению.

Rich Messages (Bot API 10.1) считают лимит иначе — по символам markdown,
поэтому у них свой лимит и обычный `len()`.
"""

from bestfiend.telegram.errors import TelegramContentInvariantError


# Жёсткий лимит Telegram на длину plain-текстового сообщения (UTF-16 code units).
TELEGRAM_TEXT_LIMIT: int = 4096

# Лимит Rich Message на длину markdown; метрика — code points Python (len()).
RICH_MESSAGE_CHAR_LIMIT: int = 32768

# Символ вне BMP занимает две UTF-16 единицы.
_ASTRAL_CODE_UNITS = 2
_BMP_MAX_CODE_POINT = 0xFFFF


def utf16_len(text: str) -> int:
    """Возвращает длину текста в UTF-16 code units — так её считает Telegram."""
    return len(text) + sum(1 for c in text if ord(c) > _BMP_MAX_CODE_POINT)


def chunk_by_utf16_limit(text: str, limit: int = TELEGRAM_TEXT_LIMIT) -> list[str]:
    """Режет текст на куски, каждый из которых влезает в лимит UTF-16 единиц.

    Склейка кусков возвращает исходный текст без потерь: `"".join(chunks) == text`.
    """
    _ensure_limit_fits_astral_char(limit)
    if not text:
        return []

    chunks: list[str] = []
    chunk_start = 0
    chunk_units = 0
    for index, char in enumerate(text):
        char_units = _char_code_units(char)
        if chunk_units + char_units > limit:
            chunks.append(text[chunk_start:index])
            chunk_start = index
            chunk_units = 0
        chunk_units += char_units
    chunks.append(text[chunk_start:])
    return chunks


def tail_by_utf16_limit(text: str, limit: int = TELEGRAM_TEXT_LIMIT) -> str:
    """Возвращает хвост текста, влезающий в лимит UTF-16 единиц."""
    _ensure_limit_fits_astral_char(limit)
    if not text:
        return ""

    tail_units = 0
    for offset, char in enumerate(reversed(text), start=1):
        tail_units += _char_code_units(char)
        if tail_units > limit:
            return text[len(text) - offset + 1 :]
    return text


def _char_code_units(char: str) -> int:
    """Считает, сколько UTF-16 единиц занимает один символ."""
    return _ASTRAL_CODE_UNITS if ord(char) > _BMP_MAX_CODE_POINT else 1


def _ensure_limit_fits_astral_char(limit: int) -> None:
    """Проверяет, что в лимит влезает хотя бы один символ любой ширины."""
    if limit < _ASTRAL_CODE_UNITS:
        raise TelegramContentInvariantError(
            f"лимит UTF-16 единиц должен быть не меньше {_ASTRAL_CODE_UNITS}, "
            f"получен {limit}"
        )
