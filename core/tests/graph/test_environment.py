"""Тесты render_environment: валидная tz, кривая tz (fallback UTC), отсутствие tz."""

from bestfiend.graph.prompts import render_environment


def test_environment_valid_timezone_renders_local_time() -> None:
    """Валидная tz: время локальное, лейбл несёт имя зоны и UTC-оффсет."""
    text = render_environment(timezone="Europe/Moscow", city="Москва")

    assert "Timezone: Europe/Moscow (UTC+03:00)" in text
    assert "Time: Unknown" not in text
    assert "Location: Москва" in text


def test_environment_invalid_timezone_falls_back_to_utc() -> None:
    """Кривая tz из профиля не роняет рендер: время в UTC, лейбл честно Unknown."""
    text = render_environment(timezone="Mars/Olympus_Mons", city="Москва")

    assert "Timezone: Unknown (time shown in UTC)" in text
    # Время всё же отрендерено (опора для темпоральных ответов), не Unknown.
    assert "Time: Unknown" not in text
    assert "Weekday: Unknown" not in text


def test_environment_empty_timezone_string_falls_back_to_utc() -> None:
    """Пустая строка tz — тоже fallback, не исключение."""
    text = render_environment(timezone="")

    assert "Timezone: Unknown (time shown in UTC)" in text


def test_environment_no_timezone_all_unknown() -> None:
    """Без tz время не выдумывается: все временные поля Unknown."""
    text = render_environment()

    assert "Time: Unknown" in text
    assert "Timezone: Unknown" in text
    assert "Weekday: Unknown" in text
    assert "Location: Unknown" in text
