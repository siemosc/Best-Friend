"""Подсчёт токенов через tiktoken — domain-less примитив (единый счёт токенов)."""

import tiktoken


_TOKEN_ENCODING = tiktoken.get_encoding("cl100k_base")


def count_tokens(text: str) -> int:
    """Количество токенов в тексте (cl100k_base)."""
    return len(_TOKEN_ENCODING.encode(text))
