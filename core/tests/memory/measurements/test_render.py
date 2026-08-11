"""Рендер агрегатов: value-метрики, события-счётчики, бакеты, обрезка."""

from datetime import UTC, datetime

from bestfiend.memory.measurements.render import render_aggregates
from tests.memory.fakes import make_metric_aggregate


def test_value_metric_line_carries_stats_and_period() -> None:
    """Метрика со значениями: последнее с единицей, avg/min/max/sum, период."""
    aggregate = make_metric_aggregate(
        "вес",
        count=12,
        last_value=70.2,
        unit="kg",
        first_at=datetime(2026, 6, 1, 9, 0, tzinfo=UTC),
        last_at=datetime(2026, 6, 30, 9, 30, tzinfo=UTC),
    )

    line = render_aggregates([aggregate])

    assert line.startswith("- вес: записей 12")
    assert "последнее 70.2 kg (2026-06-30 09:30)" in line
    assert "avg 70.2 kg" in line
    assert "период 2026-06-01 — 2026-06-30" in line


def test_event_metric_line_skips_value_stats() -> None:
    """Событие-счётчик (value NULL): количество и давность, без avg/min/max."""
    aggregate = make_metric_aggregate(
        "gym",
        count=3,
        last_at=datetime(2026, 6, 28, 18, 0, tzinfo=UTC),
    )

    line = render_aggregates([aggregate])

    assert "gym: записей 3" in line
    assert "последняя 2026-06-28 18:00" in line
    assert "avg" not in line


def test_trailing_zeros_stripped() -> None:
    """70.00 рендерится как 70 — без хвостовых нулей."""
    aggregate = make_metric_aggregate("вес", last_value=70.0, unit="kg")

    assert "последнее 70 kg" in render_aggregates([aggregate])


def test_week_bucket_label_and_no_period() -> None:
    """Бакет недели: подпись «неделя с <понедельник>», строка без «период»."""
    aggregate = make_metric_aggregate(
        "sleep_hours",
        count=7,
        last_value=7.5,
        unit="h",
        bucket_start=datetime(2026, 6, 1, tzinfo=UTC),
    )

    line = render_aggregates([aggregate], bucket="week")

    assert "sleep_hours · неделя с 2026-06-01" in line
    assert "период" not in line


def test_clipping_keeps_freshest_tail() -> None:
    """Строк больше потолка → остаётся свежий хвост с пометкой об обрезке."""
    aggregates = [
        make_metric_aggregate(
            "вес", bucket_start=datetime(2026, 1, 1 + day, tzinfo=UTC)
        )
        for day in range(5)
    ]

    text = render_aggregates(aggregates, bucket="day", max_rows=3)

    assert "(показаны последние 3 строк из 5)" in text
    assert "2026-01-05" in text  # свежий бакет остался
    assert "2026-01-01" not in text  # старейший обрезан


def test_empty_aggregates_render_empty() -> None:
    """Пустой список агрегатов → пустая строка (ответ формирует вызывающий)."""
    assert render_aggregates([]) == ""
