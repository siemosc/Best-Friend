"""Парсер временных маркеров: ru-паттерны → полуоткрытые UTC-диапазоны."""

from datetime import UTC, datetime

import pytest

from bestfiend.memory.recall.time_markers import parse_time_range


# Среда 2026-06-10 14:30 UTC: неделя началась в понедельник 2026-06-08.
_NOW = datetime(2026, 6, 10, 14, 30, tzinfo=UTC)


@pytest.mark.parametrize(
    ("text", "expected_start", "expected_end"),
    [
        (
            "что мы делали вчера?",
            datetime(2026, 6, 9, tzinfo=UTC),
            datetime(2026, 6, 10, tzinfo=UTC),
        ),
        (
            "что было сегодня",
            datetime(2026, 6, 10, tzinfo=UTC),
            datetime(2026, 6, 11, tzinfo=UTC),
        ),
        (
            "напомни, что обсуждали на прошлой неделе",
            datetime(2026, 6, 1, tzinfo=UTC),
            datetime(2026, 6, 8, tzinfo=UTC),
        ),
        (
            "3 дня назад был разговор",
            datetime(2026, 6, 7, tzinfo=UTC),
            datetime(2026, 6, 8, tzinfo=UTC),
        ),
        (
            "что решили в марте?",
            datetime(2026, 3, 1, tzinfo=UTC),
            datetime(2026, 4, 1, tzinfo=UTC),
        ),
        (
            "встреча в марте 2025",
            datetime(2025, 3, 1, tzinfo=UTC),
            datetime(2025, 4, 1, tzinfo=UTC),
        ),
        (
            "что происходило в 2025 году",
            datetime(2025, 1, 1, tzinfo=UTC),
            datetime(2026, 1, 1, tzinfo=UTC),
        ),
        (
            "итоги в прошлом месяце",
            datetime(2026, 5, 1, tzinfo=UTC),
            datetime(2026, 6, 1, tzinfo=UTC),
        ),
    ],
)
def test_markers_parse_to_half_open_ranges(
    text: str, expected_start: datetime, expected_end: datetime
) -> None:
    """Маркер → ожидаемый полуоткрытый диапазон [start, end)."""
    parsed = parse_time_range(text, _NOW)

    assert parsed == (expected_start, expected_end)


def test_future_month_without_year_resolves_to_past() -> None:
    """Месяц без года, ещё не наступивший в этом году → прошлый год."""
    parsed = parse_time_range("что было в декабре?", _NOW)

    assert parsed == (
        datetime(2025, 12, 1, tzinfo=UTC),
        datetime(2026, 1, 1, tzinfo=UTC),
    )


def test_text_without_markers_returns_none() -> None:
    """Обычный вопрос без временных маркеров → None (time-ветка не участвует)."""
    assert parse_time_range("как настроить pgvector?", _NOW) is None
    assert parse_time_range("расскажи про проект", _NOW) is None
