"""Тесты парсера ACL `parse_allowed_user_ids` (env/config → allowlist)."""

import pytest

from bestfiend.telegram.allowed_users import parse_allowed_user_ids


def test_none_stays_none() -> None:
    """Отсутствие настройки — фильтр выключен."""
    assert parse_allowed_user_ids(None) is None


def test_text_parses_ids_and_strips_spaces() -> None:
    """Строка из env парсится с обрезкой пробелов вокруг элементов."""
    assert parse_allowed_user_ids("123, 456 ,789") == [123, 456, 789]


def test_text_empty_and_separators_only_give_none() -> None:
    """Пустая строка и строка из одних запятых — фильтра нет."""
    assert parse_allowed_user_ids("") is None
    assert parse_allowed_user_ids(" , ,") is None


def test_text_with_invalid_element_fails_fast() -> None:
    """Опечатка в env-строке — ошибка старта, а не тихо открытый бот."""
    with pytest.raises(ValueError, match="abc"):
        parse_allowed_user_ids("123,abc")


def test_text_with_space_separator_fails_fast() -> None:
    """Пробел вместо запятой — ошибка старта, а не ACL из None."""
    with pytest.raises(ValueError):
        parse_allowed_user_ids("123 456")


def test_list_of_ints_passes_through() -> None:
    """Программный список чисел возвращается как есть."""
    assert parse_allowed_user_ids([1, 2]) == [1, 2]


def test_list_drops_invalid_elements() -> None:
    """Список с мусором отдаёт только валидные элементы."""
    assert parse_allowed_user_ids(["1", "x", "3"]) == [1, 3]


def test_list_of_only_invalid_elements_gives_none() -> None:
    """Список без единого валидного элемента — фильтра нет."""
    assert parse_allowed_user_ids(["x"]) is None


def test_empty_list_stays_empty() -> None:
    """Пустой программный список остаётся пустым, не превращаясь в None."""
    assert parse_allowed_user_ids([]) == []


def test_scalar_int_wraps_into_list() -> None:
    """Одиночное число оборачивается в список."""
    assert parse_allowed_user_ids(42) == [42]  # type: ignore[arg-type]


def test_scalar_garbage_gives_none() -> None:
    """Непреобразуемый скаляр — фильтра нет."""
    assert parse_allowed_user_ids(object()) is None  # type: ignore[arg-type]
