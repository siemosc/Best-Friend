"""Корреляционная пара запроса: request_id + user_id.

Нормализуется на ingress (Telegram) и течёт через graph до persist как единый
scope запроса; `request_id` = Langfuse session_id. Trace-атрибуты проставляет
caller — здесь только корреляция.
"""

from dataclasses import dataclass
from uuid import UUID


class RequestCorrelationError(ValueError):
    """Ошибка нормализации корреляционной пары запроса."""


@dataclass(slots=True, frozen=True)
class RequestCorrelation:
    """Нормализованная корреляционная пара запроса."""

    request_id: str
    user_id: str


def ensure_request_correlation(
    *,
    request_id: str,
    user_id: UUID | str,
) -> RequestCorrelation:
    """Нормализует пару request_id/user_id в корреляцию запроса."""
    return RequestCorrelation(
        request_id=_require_non_empty_str(request_id, field_name="request_id"),
        user_id=_require_non_empty_str(str(user_id), field_name="user_id"),
    )


def _require_non_empty_str(value: str, *, field_name: str) -> str:
    """Обрезает краевые пробелы; пустое значение → RequestCorrelationError."""
    normalized_value = (value or "").strip()
    if not normalized_value:
        raise RequestCorrelationError(f"Field '{field_name}' must be non-empty.")
    return normalized_value
