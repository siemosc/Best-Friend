"""AIConfig — passthrough dict-обёртка для параметров модельного вызова.

Общий конфиг для слоёв llm и embeddings. Хранит произвольный dict[str, Any] из
БД (таблица models; для chat-модели графа — per-user resolve в model_registry).
Код, которому нужны конкретные значения, читает через typed properties.
as_kwargs() отдаёт генерационные параметры (без client- и call-time-полей) для
проброса в фабрики llm/embeddings.
"""

from typing import Any

from bestfiend.ai.errors import AIConfigError


class AIConfig:
    """Passthrough-конфиг модельного вызова."""

    _CLIENT_FIELDS = frozenset(
        {
            "provider",
            "model",
            "timeout_s",
            "api_key",
            "api_base",
        }
    )
    _CALL_TIME_FIELDS = frozenset({"tools", "stream", "messages"})
    # Метаданные для нашей логики (read-раскладка бюджета, vision-гейт),
    # не параметры вызова — в конструкторы провайдеров не утекают.
    _META_FIELDS = frozenset({"context_window", "supports_vision"})

    __slots__ = ("_data",)

    def __init__(self, data: dict[str, Any]) -> None:
        provider = data.get("provider")
        model = data.get("model")
        if not isinstance(provider, str) or not provider:
            raise AIConfigError("AIConfig requires non-empty string 'provider'")
        if not isinstance(model, str) or not model:
            raise AIConfigError("AIConfig requires non-empty string 'model'")
        self._data = dict(data)

    @property
    def provider(self) -> str:
        return self._data["provider"]

    @property
    def model(self) -> str:
        return self._data["model"]

    @property
    def timeout_s(self) -> float | None:
        return _as_float(self._data.get("timeout_s"), field="timeout_s")

    @property
    def api_key(self) -> str | None:
        return self._data.get("api_key")

    @property
    def api_base(self) -> str | None:
        return self._data.get("api_base")

    @property
    def max_tokens(self) -> int | None:
        return _as_int(self._data.get("max_tokens"), field="max_tokens")

    @property
    def context_window(self) -> int | None:
        """Размер контекстного окна модели — для read-раскладки бюджета памяти."""
        return _as_int(self._data.get("context_window"), field="context_window")

    @property
    def supports_vision(self) -> bool:
        """Модель умеет принимать изображения — гейт нативной передачи фото."""
        return bool(self._data.get("supports_vision", False))

    def as_kwargs(self) -> dict[str, Any]:
        """Всё кроме client-handled и call-time fields, без None."""
        excluded = self._CLIENT_FIELDS | self._CALL_TIME_FIELDS | self._META_FIELDS
        return {
            k: v for k, v in self._data.items() if k not in excluded and v is not None
        }


def _as_int(value: Any, *, field: str) -> int | None:
    """Конвертирует значение конфига в int; кривое → AIConfigError (fail-open у ловца)."""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise AIConfigError(
            f"AIConfig field '{field}' is not an integer: {value!r}"
        ) from exc


def _as_float(value: Any, *, field: str) -> float | None:
    """Конвертирует значение конфига в float; кривое → AIConfigError."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise AIConfigError(
            f"AIConfig field '{field}' is not a number: {value!r}"
        ) from exc
