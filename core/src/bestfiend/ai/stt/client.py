"""Адаптер транскрипции к OpenAI-совместимому эндпоинту /v1/audio/transcriptions.

Целевой сервер — self-hosted vLLM с ASR-моделью. Клиент fail-soft: любой сбой
источника (сеть, не-2xx, нечитаемый ответ) гасится в warning и None — решение,
что делать без транскрипта, принимает вызывающий слой. httpx.AsyncClient
короткоживущий per-вызов: транскрипции редкие, keep-alive не окупает владение
lifecycle'ом клиента.
"""

import httpx
from loguru import logger
import orjson


_TRANSCRIPTIONS_PATH = "/v1/audio/transcriptions"
# Тело ответа может быть длинным — в лог берём начало, чтобы увидеть форму ошибки.
_BODY_TRIM_CHARS = 300


class OpenAICompatibleSpeechTranscriber:
    """Транскрипция через OpenAI-совместимый multipart-эндпоинт."""

    __slots__ = ("_endpoint", "_model", "_timeout_s", "_transport")

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        timeout_s: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        # base_url — origin сервера без /v1; путь эндпоинта знает адаптер, не конфиг.
        self._endpoint = base_url.rstrip("/") + _TRANSCRIPTIONS_PATH
        self._model = model
        self._timeout_s = timeout_s
        # Подмена транспорта нужна тестам; в проде остаётся дефолтный.
        self._transport = transport

    async def transcribe(self, audio: bytes, filename: str) -> str | None:
        """Транскрибирует аудио; пустая строка — речи нет, None — отказ источника."""
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout_s, transport=self._transport
            ) as http:
                response = await http.post(
                    self._endpoint,
                    files={"file": (filename, audio)},
                    data={"model": self._model},
                )
        except (httpx.HTTPError, httpx.InvalidURL) as exc:
            # InvalidURL ловим отдельно: он не наследник HTTPError, а кривой
            # STT_URL из env не должен ронять обработку сообщения.
            logger.warning(
                "stt: запрос к {} не удался ({}: {})",
                self._endpoint,
                type(exc).__name__,
                exc,
            )
            return None

        if not response.is_success:
            logger.warning(
                "stt: {} ответил HTTP {} — {}",
                self._endpoint,
                response.status_code,
                response.text[:_BODY_TRIM_CHARS],
            )
            return None
        return self._extract_text(response.content)

    def _extract_text(self, content: bytes) -> str | None:
        """Достаёт поле `text` из тела ответа; нечитаемое тело → None."""
        try:
            payload = orjson.loads(content)
        except orjson.JSONDecodeError:
            logger.warning(
                "stt: {} вернул не-JSON — {}",
                self._endpoint,
                content[:_BODY_TRIM_CHARS],
            )
            return None

        text = payload.get("text") if isinstance(payload, dict) else None
        if not isinstance(text, str):
            logger.warning(
                "stt: в ответе {} нет строкового поля 'text' — {}",
                self._endpoint,
                content[:_BODY_TRIM_CHARS],
            )
            return None
        return text.strip()
