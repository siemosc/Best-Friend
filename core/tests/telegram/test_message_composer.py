"""Тесты композиции markdown финального ответа и нарезки под лимит Rich Message.

Фокус: обвязка прогресс-лога в `<details>`, плоский fallback и сплит по
границам параграфов — с запретом резать внутри fenced-блока кода.
"""

import pytest

from bestfiend.telegram.errors import TelegramContentInvariantError
from bestfiend.telegram.formatters.message_composer import (
    THINKING_DRAFT_MARKDOWN,
    compose_final_markdown,
    compose_plain_fallback,
    split_rich_markdown,
)
from bestfiend.telegram.formatters.text_limits import RICH_MESSAGE_CHAR_LIMIT


_PROGRESS_LINES = ["⏳ Ищу", "⏳ Читаю"]


def test_final_wraps_progress_log_into_details() -> None:
    """Непустой лог → `<details>` с логом, затем контент через пустую строку."""
    markdown = compose_final_markdown(progress_lines=_PROGRESS_LINES, content="Ответ")
    assert markdown == (
        "<details><summary>Как я работал</summary>\n\n"
        "⏳ Ищу\n⏳ Читаю\n\n</details>\n\nОтвет"
    )


def test_final_without_progress_returns_bare_content() -> None:
    """Пустой лог → контент без обвязки."""
    assert compose_final_markdown(progress_lines=[], content="Ответ") == "Ответ"


def test_final_with_empty_content_returns_details_only() -> None:
    """Пустой контент при непустом логе → только `<details>`, без хвостового отступа."""
    markdown = compose_final_markdown(progress_lines=_PROGRESS_LINES, content="")
    assert markdown.endswith("</details>")
    assert "⏳ Ищу\n⏳ Читаю" in markdown


def test_plain_fallback_puts_log_before_content() -> None:
    """Плоский fallback: строки лога, пустая строка, контент как есть."""
    plain = compose_plain_fallback(progress_lines=_PROGRESS_LINES, content="Ответ")
    assert plain == "⏳ Ищу\n⏳ Читаю\n\nОтвет"


def test_plain_fallback_without_progress_returns_bare_content() -> None:
    """Пустой лог → плоский текст равен контенту."""
    assert compose_plain_fallback(progress_lines=[], content="Ответ") == "Ответ"


def test_plain_fallback_with_empty_content_returns_log_only() -> None:
    """Пустой контент → только лог, без висящей пустой строки."""
    plain = compose_plain_fallback(progress_lines=_PROGRESS_LINES, content="")
    assert plain == "⏳ Ищу\n⏳ Читаю"


def test_thinking_draft_is_tg_thinking_block() -> None:
    """Draft «думаю» — нативный тег Telegram, не произвольный текст."""
    assert THINKING_DRAFT_MARKDOWN.startswith("<tg-thinking>")
    assert THINKING_DRAFT_MARKDOWN.endswith("</tg-thinking>")


def test_split_short_markdown_returns_single_part() -> None:
    """Текст в пределах лимита не режется."""
    assert split_rich_markdown("короткий ответ") == ["короткий ответ"]


def test_split_empty_markdown_returns_single_empty_part() -> None:
    """Пустой markdown даёт одну пустую часть — отправлять нечего, но список не пуст."""
    assert split_rich_markdown("") == [""]


def test_split_cuts_on_paragraph_boundary() -> None:
    """Разрез идёт по границе параграфов, сам разделитель схлопывается."""
    markdown = "aaaa\n\nbbbb\n\ncccc"
    assert split_rich_markdown(markdown, limit=10) == ["aaaa\n\nbbbb", "cccc"]


def test_split_ignores_paragraph_boundary_inside_code_fence() -> None:
    """Пустая строка внутри ```-блока не считается границей — блок не рвётся."""
    markdown = "```\ncode\n\nmore\n```\n\nafter"
    parts = split_rich_markdown(markdown, limit=20)
    assert parts == ["```\ncode\n\nmore\n```", "after"]


def test_split_hard_cuts_oversized_paragraph() -> None:
    """Параграф длиннее бюджета режется жёстко — иначе часть не влезет в лимит."""
    markdown = "x" * 25
    parts = split_rich_markdown(markdown, limit=10)
    assert parts == ["x" * 10, "x" * 10, "x" * 5]


def test_split_hard_cuts_when_only_boundary_is_inside_fence() -> None:
    """Единственная граница внутри fence не используется — режем жёстко по бюджету."""
    markdown = "```\ncode\n\nmore\n```"
    parts = split_rich_markdown(markdown, limit=12)
    assert parts[0] == markdown[:12]


def test_split_first_part_reserve_shrinks_first_budget() -> None:
    """Резерв под обвязку уменьшает бюджет первой части, остальные считаются по лимиту."""
    markdown = "aaaa\n\nbbbb\n\ncccc"
    assert split_rich_markdown(markdown, limit=10, first_part_reserve=4) == [
        "aaaa",
        "bbbb\n\ncccc",
    ]


def test_split_keeps_every_part_within_budget() -> None:
    """Каждая часть влезает в свой бюджет: первая — с вычетом резерва."""
    markdown = "\n\n".join(f"параграф {i} " + "т" * 40 for i in range(30))
    reserve = 30
    parts = split_rich_markdown(markdown, limit=100, first_part_reserve=reserve)
    assert len(parts[0]) <= 100 - reserve
    assert all(len(part) <= 100 for part in parts[1:])


def test_split_preserves_all_text_content() -> None:
    """Сплит теряет только схлопнутые переносы — остальной текст сохраняется целиком."""
    markdown = "\n\n".join(f"параграф {i} " + "т" * 40 for i in range(30))
    parts = split_rich_markdown(markdown, limit=100)
    assert "".join(parts).replace("\n", "") == markdown.replace("\n", "")


def test_split_rejects_reserve_larger_than_limit() -> None:
    """Резерв не оставляет бюджета первой части → доменная ошибка."""
    with pytest.raises(TelegramContentInvariantError):
        split_rich_markdown("текст", limit=10, first_part_reserve=10)


def test_split_default_limit_is_rich_message_limit() -> None:
    """Дефолтный лимит сплита — лимит Rich Message, не plain-лимит Telegram."""
    markdown = "a" * (RICH_MESSAGE_CHAR_LIMIT + 1)
    parts = split_rich_markdown(markdown)
    assert [len(part) for part in parts] == [RICH_MESSAGE_CHAR_LIMIT, 1]
