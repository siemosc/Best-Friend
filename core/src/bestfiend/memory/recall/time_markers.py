"""Детерминированный парсер временных маркеров запроса (ru), без LLM.

Возвращает полуоткрытый диапазон [start, end): начало входит, конец — нет.
Нераспознанный текст → None: time-ветка recall просто не участвует,
деградации нет. Узкий набор паттернов — осознанно: ложный диапазон хуже
отсутствия (сузил бы выдачу мимо вопроса).
"""

from datetime import UTC, datetime, timedelta
import re
from typing import Final


_MONTHS: Final[dict[str, int]] = {
    "январ": 1,
    "феврал": 2,
    "март": 3,
    "апрел": 4,
    "мая": 5,
    "мае": 5,
    "май": 5,
    "июн": 6,
    "июл": 7,
    "август": 8,
    "сентябр": 9,
    "октябр": 10,
    "ноябр": 11,
    "декабр": 12,
}

# Порядок проверки: от специфичных к общим (месяц с годом раньше голого года).
_RELATIVE_DAYS: Final[dict[str, int]] = {"сегодня": 0, "вчера": 1, "позавчера": 2}

_AGO_UNITS: Final[dict[str, int]] = {"дн": 1, "недел": 7, "месяц": 30}

_AGO_RE: Final[re.Pattern[str]] = re.compile(
    r"(\d{1,3})\s+(дн(?:я|ей)|недел[июь]|месяц(?:а|ев)?)\s+назад"
)
_MONTH_RE: Final[re.Pattern[str]] = re.compile(
    r"\bв\s+("
    r"январе|феврале|марте|апреле|мае|июне|июле|августе|сентябре|октябре|ноябре|декабре"
    r")(?:\s+(\d{4}))?(?:\s+год[ауе]?)?"
)
_YEAR_RE: Final[re.Pattern[str]] = re.compile(r"\bв\s+(\d{4})(?:\s+год[ауе]?)?")


def parse_time_range(text: str, now: datetime) -> tuple[datetime, datetime] | None:
    """Временной маркер текста → полуоткрытый диапазон [start, end) или None."""
    lowered = text.casefold()
    today = _day_start(now)
    resolvers = (
        _relative_day_range,
        _ago_range,
        _named_period_range,
        _calendar_period_range,
    )
    for resolver in resolvers:
        resolved = resolver(lowered, today, now)
        if resolved is not None:
            return resolved
    return None


def _relative_day_range(
    text: str,
    today: datetime,
    _now: datetime,
) -> tuple[datetime, datetime] | None:
    """Распознаёт сегодня, вчера и позавчера."""
    for word, days_back in _RELATIVE_DAYS.items():
        if word in text:
            start = today - timedelta(days=days_back)
            return start, start + timedelta(days=1)
    return None


def _ago_range(
    text: str,
    today: datetime,
    _now: datetime,
) -> tuple[datetime, datetime] | None:
    """Распознаёт конструкции вида «N дней назад»."""
    match = _AGO_RE.search(text)
    if match is None:
        return None
    amount = int(match.group(1))
    unit_days = next(
        days for stem, days in _AGO_UNITS.items() if match.group(2).startswith(stem)
    )
    start = today - timedelta(days=amount * unit_days)
    return start, start + timedelta(days=unit_days)


def _named_period_range(
    text: str,
    today: datetime,
    _now: datetime,
) -> tuple[datetime, datetime] | None:
    """Распознаёт именованные относительные недели и месяцы."""
    if "на прошлой неделе" in text:
        week_start = _week_start(today) - timedelta(weeks=1)
        return week_start, week_start + timedelta(weeks=1)
    if "на этой неделе" in text:
        week_start = _week_start(today)
        return week_start, week_start + timedelta(weeks=1)
    if "в прошлом месяце" in text:
        this_month = today.replace(day=1)
        start = _month_back(this_month)
        return start, this_month
    if "в этом месяце" in text:
        start = today.replace(day=1)
        return start, _month_forward(start)
    return None


def _calendar_period_range(
    text: str,
    _today: datetime,
    now: datetime,
) -> tuple[datetime, datetime] | None:
    """Распознаёт календарный месяц или год."""
    match = _MONTH_RE.search(text)
    if match is not None:
        month = next(
            num for stem, num in _MONTHS.items() if match.group(1).startswith(stem)
        )
        year = int(match.group(2)) if match.group(2) else _nearest_past_year(month, now)
        start = datetime(year, month, 1, tzinfo=UTC)
        return start, _month_forward(start)

    match = _YEAR_RE.search(text)
    if match is not None:
        year = int(match.group(1))
        return (
            datetime(year, 1, 1, tzinfo=UTC),
            datetime(year + 1, 1, 1, tzinfo=UTC),
        )
    return None


def _day_start(moment: datetime) -> datetime:
    return moment.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)


def _week_start(day: datetime) -> datetime:
    return day - timedelta(days=day.weekday())


def _month_back(month_start: datetime) -> datetime:
    if month_start.month == 1:
        return month_start.replace(year=month_start.year - 1, month=12)
    return month_start.replace(month=month_start.month - 1)


def _month_forward(month_start: datetime) -> datetime:
    if month_start.month == 12:
        return month_start.replace(year=month_start.year + 1, month=1)
    return month_start.replace(month=month_start.month + 1)


def _nearest_past_year(month: int, now: datetime) -> int:
    """Месяц без года: текущий год, если месяц уже наступил, иначе прошлый."""
    return now.year if month <= now.month else now.year - 1
