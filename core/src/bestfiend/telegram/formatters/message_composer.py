"""Композиция markdown финального ответа и draft'а для Rich Messages Telegram.

Telegram (Bot API 10.1) рендерит GFM сам, поэтому канал отдаёт ему готовый
markdown, а не entity-разметку: прогресс-лог сворачивается в `<details>`,
«думаю» — в `<tg-thinking>`. Здесь только сборка строк и нарезка под лимит
Rich Message; отправка и деградация до plain — в outbound_delivery.
"""

from bisect import bisect_right

from bestfiend.telegram.errors import TelegramContentInvariantError
from bestfiend.telegram.formatters.text_limits import RICH_MESSAGE_CHAR_LIMIT


# Плейсхолдер draft'а на время работы модели: Telegram рисует его «думающим».
THINKING_DRAFT_MARKDOWN: str = "<tg-thinking>Думаю…</tg-thinking>"

_PROGRESS_SUMMARY = "Как я работал"
_BLOCK_SEPARATOR = "\n\n"
_CODE_FENCE = "```"


def compose_final_markdown(*, progress_lines: list[str], content: str) -> str:
    """Собирает markdown финального ответа: прогресс-лог в `<details>` перед контентом."""
    if not progress_lines:
        return content
    log = "\n".join(progress_lines)
    details = (
        f"<details><summary>{_PROGRESS_SUMMARY}</summary>"
        f"{_BLOCK_SEPARATOR}{log}{_BLOCK_SEPARATOR}</details>"
    )
    if not content:
        return details
    return f"{details}{_BLOCK_SEPARATOR}{content}"


def compose_plain_fallback(*, progress_lines: list[str], content: str) -> str:
    """Собирает плоский текст финального ответа для отправки без rich-разметки."""
    if not progress_lines:
        return content
    log = "\n".join(progress_lines)
    if not content:
        return log
    return f"{log}{_BLOCK_SEPARATOR}{content}"


def split_rich_markdown(
    markdown: str,
    limit: int = RICH_MESSAGE_CHAR_LIMIT,
    *,
    first_part_reserve: int = 0,
) -> list[str]:
    """Режет markdown на части по границам параграфов вне блоков кода.

    Бюджет первой части уменьшен на `first_part_reserve` — место под обвязку,
    которую вызывающий добавит к ней позже (вместе с её собственным отступом от
    контента). Граница `\\n\\n` на месте разреза схлопывается, остальной текст
    сохраняется целиком.
    """
    _ensure_split_budget(limit, first_part_reserve)

    break_offsets = _paragraph_break_offsets(markdown)
    parts: list[str] = []
    cursor = 0
    budget = limit - first_part_reserve
    while len(markdown) - cursor > budget:
        break_offset = _last_break_within(break_offsets, cursor, cursor + budget)
        if break_offset is None:
            # Параграф длиннее бюджета — режем жёстко, иначе часть не влезет в лимит.
            parts.append(markdown[cursor : cursor + budget])
            cursor += budget
        else:
            parts.append(markdown[cursor:break_offset])
            cursor = break_offset + len(_BLOCK_SEPARATOR)
        budget = limit

    tail = markdown[cursor:]
    if tail or not parts:
        parts.append(tail)
    return parts


def _paragraph_break_offsets(markdown: str) -> list[int]:
    """Собирает позиции разделителей параграфов, лежащих вне fenced-блоков кода."""
    offsets: list[int] = []
    inside_fence = False
    index = 0
    while index < len(markdown):
        if markdown.startswith(_CODE_FENCE, index):
            inside_fence = not inside_fence
            index += len(_CODE_FENCE)
            continue
        if not inside_fence and markdown.startswith(_BLOCK_SEPARATOR, index):
            offsets.append(index)
        index += 1
    return offsets


def _last_break_within(
    break_offsets: list[int], cursor: int, max_offset: int
) -> int | None:
    """Ищет самый дальний разделитель параграфов, дающий непустую часть в бюджете."""
    candidate_index = bisect_right(break_offsets, max_offset) - 1
    if candidate_index < 0:
        return None
    candidate = break_offsets[candidate_index]
    return candidate if candidate > cursor else None


def _ensure_split_budget(limit: int, first_part_reserve: int) -> None:
    """Проверяет, что после вычета резерва у первой части остаётся бюджет."""
    if limit - first_part_reserve < 1:
        raise TelegramContentInvariantError(
            f"резерв обвязки {first_part_reserve} не оставляет бюджета "
            f"в лимите rich-сообщения {limit}"
        )
