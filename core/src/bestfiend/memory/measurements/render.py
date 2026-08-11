"""Текстовый рендер агрегатов измерений: тулза memory_stats и sleep-сводки недели."""

from bestfiend.memory.measurements.contracts import MeasurementBucket, MetricAggregate


# Потолок строк выдачи: годовая разбивка по дням не должна раздувать контекст.
_MAX_ROWS_DEFAULT = 40


def render_aggregates(
    aggregates: list[MetricAggregate],
    *,
    bucket: MeasurementBucket | None = None,
    max_rows: int = _MAX_ROWS_DEFAULT,
) -> str:
    """Агрегаты → компактные строки для LLM, свежие бакеты приоритетнее при обрезке."""
    if not aggregates:
        return ""
    clipped = ""
    visible = aggregates
    if len(aggregates) > max_rows:
        # Сортировка репозитория — ASC по времени: хвост списка = свежие бакеты.
        visible = aggregates[-max_rows:]
        clipped = f"(показаны последние {max_rows} строк из {len(aggregates)})\n"
    lines = "\n".join(_render_line(aggregate, bucket) for aggregate in visible)
    return f"{clipped}{lines}"


def _render_line(aggregate: MetricAggregate, bucket: MeasurementBucket | None) -> str:
    """Одна строка агрегата: метрика (или метрика в бакете) + статистика."""
    label = aggregate.metric
    if aggregate.bucket_start is not None:
        label = f"{aggregate.metric} · {_bucket_label(aggregate, bucket)}"

    parts = [f"записей {aggregate.count}"]
    if aggregate.last_value is not None:
        last_at = aggregate.last_at.strftime("%Y-%m-%d %H:%M")
        parts.append(
            f"последнее {_value(aggregate.last_value, aggregate.unit)} ({last_at})"
        )
        stats = ", ".join(
            f"{name} {_value(value, aggregate.unit)}"
            for name, value in (
                ("avg", aggregate.value_avg),
                ("min", aggregate.value_min),
                ("max", aggregate.value_max),
                ("sum", aggregate.value_sum),
            )
            if value is not None
        )
        if stats:
            parts.append(stats)
    else:
        # Событие-счётчик: значений нет, факт и давность важнее статистики.
        parts.append(f"последняя {aggregate.last_at.strftime('%Y-%m-%d %H:%M')}")
    if aggregate.bucket_start is None:
        period_start = aggregate.first_at.date().isoformat()
        period_end = aggregate.last_at.date().isoformat()
        parts.append(f"период {period_start} — {period_end}")
    return f"- {label}: {'; '.join(parts)}"


def _bucket_label(aggregate: MetricAggregate, bucket: MeasurementBucket | None) -> str:
    """Подпись бакета по его типу: день — дата, неделя — понедельник, месяц — YYYY-MM."""
    start = aggregate.bucket_start
    if start is None:
        return ""
    if bucket == "week":
        return f"неделя с {start.date().isoformat()}"
    if bucket == "month":
        return f"{start.year:04d}-{start.month:02d}"
    return start.date().isoformat()


def _value(value: float, unit: str | None) -> str:
    """Число с единицей: без хвостовых нулей, unit через пробел."""
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return f"{text} {unit}" if unit else text
