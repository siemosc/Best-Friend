"""Контракт-тест primitives.tokenizer: единый cl100k_base счёт токенов."""

from bestfiend.primitives.tokenizer import count_tokens


def test_empty_string_is_zero_tokens() -> None:
    assert count_tokens("") == 0


def test_returns_positive_int_for_text() -> None:
    n = count_tokens("hello world")
    assert isinstance(n, int)
    assert n > 0


def test_monotonic_with_more_text() -> None:
    assert count_tokens("a b c d e f g") > count_tokens("a")
