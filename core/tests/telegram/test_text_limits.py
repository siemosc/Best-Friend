"""Тесты UTF-16 метрики и нарезки текста под лимит Telegram.

Фокус: символы вне BMP (эмодзи) стоят две UTF-16 единицы, поэтому нарезка
не должна ни разрывать такой символ, ни терять текст.
"""

import pytest

from bestfiend.telegram.errors import TelegramContentInvariantError
from bestfiend.telegram.formatters.text_limits import (
    TELEGRAM_TEXT_LIMIT,
    chunk_by_utf16_limit,
    tail_by_utf16_limit,
    utf16_len,
)


_GRINNING = "\U0001f600"  # эмодзи вне BMP: 1 code point, 2 UTF-16 единицы


def test_utf16_len_counts_bmp_chars_as_one() -> None:
    """Текст из BMP-символов: длина совпадает с len()."""
    assert utf16_len("привет abc") == len("привет abc")


def test_utf16_len_counts_astral_char_as_two() -> None:
    """Эмодзи вне BMP считается двумя UTF-16 единицами."""
    assert utf16_len(_GRINNING) == 2
    assert utf16_len(f"a{_GRINNING}b") == 4


def test_chunk_keeps_astral_char_whole_on_boundary() -> None:
    """Эмодзи не помещается в остаток лимита → уезжает целиком в следующий кусок."""
    text = f"ab{_GRINNING}cd"
    chunks = chunk_by_utf16_limit(text, limit=3)
    assert chunks == ["ab", f"{_GRINNING}c", "d"]
    assert all(utf16_len(chunk) <= 3 for chunk in chunks)


def test_chunk_join_restores_original_text() -> None:
    """Склейка кусков возвращает исходный текст без потерь."""
    text = ("шаг " + _GRINNING + " данные ") * 40
    chunks = chunk_by_utf16_limit(text, limit=17)
    assert "".join(chunks) == text
    assert all(utf16_len(chunk) <= 17 for chunk in chunks)


def test_chunk_fits_limit_returns_single_piece() -> None:
    """Текст в пределах лимита не режется."""
    assert chunk_by_utf16_limit("короткий", limit=TELEGRAM_TEXT_LIMIT) == ["короткий"]


def test_chunk_empty_text_returns_no_chunks() -> None:
    """Пустой текст не порождает кусков."""
    assert chunk_by_utf16_limit("") == []


def test_chunk_all_astral_splits_by_pairs() -> None:
    """Сплошные эмодзи при лимите 5: по два эмодзи на кусок, ни один не разорван."""
    text = _GRINNING * 5
    chunks = chunk_by_utf16_limit(text, limit=5)
    assert chunks == [_GRINNING * 2, _GRINNING * 2, _GRINNING]
    assert "".join(chunks) == text


def test_chunk_rejects_limit_below_astral_char() -> None:
    """Лимит меньше ширины эмодзи невыполним → доменная ошибка, не бесконечный цикл."""
    with pytest.raises(TelegramContentInvariantError):
        chunk_by_utf16_limit(f"a{_GRINNING}", limit=1)


def test_tail_returns_window_within_limit() -> None:
    """Хвост обрезается по лимиту UTF-16 единиц."""
    assert tail_by_utf16_limit("abcdef", limit=3) == "def"


def test_tail_keeps_astral_char_whole() -> None:
    """Эмодзи на границе окна не разрывается: он не влезает — окно короче лимита."""
    text = f"ab{_GRINNING}cd"
    tail = tail_by_utf16_limit(text, limit=3)
    assert tail == "cd"
    assert utf16_len(tail) <= 3


def test_tail_includes_astral_char_when_it_fits() -> None:
    """Эмодзи целиком влезает в окно — попадает в хвост."""
    text = f"ab{_GRINNING}cd"
    assert tail_by_utf16_limit(text, limit=4) == f"{_GRINNING}cd"


def test_tail_shorter_than_limit_returns_whole_text() -> None:
    """Текст короче лимита возвращается целиком."""
    assert tail_by_utf16_limit("abc", limit=TELEGRAM_TEXT_LIMIT) == "abc"


def test_tail_empty_text_returns_empty() -> None:
    """Пустой текст даёт пустой хвост."""
    assert tail_by_utf16_limit("") == ""
