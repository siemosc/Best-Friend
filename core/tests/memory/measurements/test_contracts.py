"""Контракты измерений: канонизация имени метрики."""

from bestfiend.memory.measurements.contracts import normalize_metric_name


def test_canonize_lowercases_and_joins_spaces() -> None:
    """Регистр вниз, пробельные серии → одно подчёркивание, края обрезаны."""
    assert normalize_metric_name(" Вес Тела ") == "вес_тела"
    assert normalize_metric_name("Sleep\t Hours") == "sleep_hours"
    assert normalize_metric_name("gym") == "gym"


def test_canonize_empty_input_gives_empty_name() -> None:
    """Пустая или пробельная строка → пустое имя (guard на стороне тулзы)."""
    assert normalize_metric_name("") == ""
    assert normalize_metric_name("   ") == ""
