"""Плейсхолдер: окружение пользователя (время + локация) для system prompt."""

from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from loguru import logger


_DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S"
_UNKNOWN = "Unknown"
_WEEKDAYS = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)
_MONTHS = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)


def render_environment(
    timezone: str | None = None,
    city: str | None = None,
    country: str | None = None,
) -> str:
    """Рендерит время и геолокацию пользователя для system prompt.

    Все поля опциональны. Отсутствующие заполняются `Unknown`, чтобы LLM
    явно видел границы знания и не выдавал серверное время за пользовательское.
    """
    # Явный маппинг дней/месяцев, чтобы вывод не зависел от системной локали.
    tz: ZoneInfo | None = None
    tz_is_fallback = False
    if timezone is not None:
        try:
            tz = ZoneInfo(timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            # Кривая tz из профиля юзера не должна валить init-ноду (каждый
            # запрос уходил бы в error): откат на UTC с честной пометкой для LLM.
            logger.warning(
                "environment: таймзона {!r} не распозналась, время рендерим в UTC: {}",
                timezone,
                exc,
            )
            tz = ZoneInfo("UTC")
            tz_is_fallback = True

    if tz is not None:
        now = datetime.now(tz)
        time_str = now.strftime(_DATETIME_FORMAT)
        # %z даёт "+0200"; приводим к ISO-8601 "+02:00" для readable UTC offset.
        offset_raw = now.strftime("%z")
        offset_str = f"{offset_raw[:3]}:{offset_raw[3:]}"
        timezone_str = (
            f"{_UNKNOWN} (time shown in UTC)"
            if tz_is_fallback
            else f"{timezone} (UTC{offset_str})"
        )
        weekday = _WEEKDAYS[now.weekday()]
        month = _MONTHS[now.month - 1]
        quarter_str = f"Q{(now.month - 1) // 3 + 1}"
    else:
        time_str = _UNKNOWN
        timezone_str = _UNKNOWN
        weekday = _UNKNOWN
        month = _UNKNOWN
        quarter_str = _UNKNOWN

    city_str = city or _UNKNOWN
    location_str = f"{city_str}, {country}" if country else city_str

    return (
        "## User's current time and location\n"
        f"Time: {time_str}\n"
        f"Timezone: {timezone_str}\n"
        f"Weekday: {weekday}\n"
        f"Month: {month}\n"
        f"Quarter: {quarter_str}\n"
        f"Location: {location_str}"
    )
