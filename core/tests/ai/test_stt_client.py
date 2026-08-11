"""OpenAICompatibleSpeechTranscriber: сборка запроса и разбор ответов STT-сервера.

Юнит через httpx.MockTransport. Проверяем контракт границы (URL эндпоинта,
multipart-состав запроса, извлечение поля `text`) и обработку чужого отказа:
не-2xx, обрыв сети, битый JSON, поле не той формы — всё в None, без исключений.
"""

from collections.abc import Callable

import httpx
import orjson
import pytest

from bestfiend.ai.stt import OpenAICompatibleSpeechTranscriber


_ORIGIN = "http://stt.example.com:8001"
_ENDPOINT = f"{_ORIGIN}/v1/audio/transcriptions"
_MODEL = "Qwen/Qwen3-ASR-1.7B"
_AUDIO = b"OggS-fake-audio-bytes"
_FILENAME = "telegram-voice-abc.ogg"

_RequestHandler = Callable[[httpx.Request], httpx.Response]


def _transcriber(
    handler: _RequestHandler, *, base_url: str = _ORIGIN
) -> OpenAICompatibleSpeechTranscriber:
    """Собирает клиента поверх MockTransport с заданным обработчиком запроса."""
    return OpenAICompatibleSpeechTranscriber(
        base_url=base_url,
        model=_MODEL,
        timeout_s=5.0,
        transport=httpx.MockTransport(handler),
    )


def _responds(status: int, content: bytes) -> _RequestHandler:
    """Обработчик, отвечающий фиксированным статусом и телом."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, content=content)

    return handler


@pytest.mark.asyncio
async def test_success_returns_text() -> None:
    transcriber = _transcriber(_responds(200, orjson.dumps({"text": "привет мир"})))
    assert await transcriber.transcribe(_AUDIO, _FILENAME) == "привет мир"


@pytest.mark.asyncio
async def test_text_is_stripped() -> None:
    transcriber = _transcriber(_responds(200, orjson.dumps({"text": "  привет \n"})))
    assert await transcriber.transcribe(_AUDIO, _FILENAME) == "привет"


@pytest.mark.asyncio
async def test_empty_text_is_empty_string_not_none() -> None:
    """Пустой транскрипт — валидный ответ «речи нет», а не отказ источника."""
    transcriber = _transcriber(_responds(200, orjson.dumps({"text": "   "})))
    assert await transcriber.transcribe(_AUDIO, _FILENAME) == ""


@pytest.mark.asyncio
async def test_server_error_returns_none() -> None:
    transcriber = _transcriber(_responds(500, b"internal error"))
    assert await transcriber.transcribe(_AUDIO, _FILENAME) is None


@pytest.mark.asyncio
async def test_network_failure_returns_none() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    transcriber = _transcriber(handler)
    assert await transcriber.transcribe(_AUDIO, _FILENAME) is None


@pytest.mark.asyncio
async def test_malformed_json_returns_none() -> None:
    transcriber = _transcriber(_responds(200, b"{not-json"))
    assert await transcriber.transcribe(_AUDIO, _FILENAME) is None


@pytest.mark.asyncio
async def test_missing_text_field_returns_none() -> None:
    transcriber = _transcriber(_responds(200, orjson.dumps({"error": "no speech"})))
    assert await transcriber.transcribe(_AUDIO, _FILENAME) is None


@pytest.mark.asyncio
async def test_non_string_text_returns_none() -> None:
    transcriber = _transcriber(_responds(200, orjson.dumps({"text": {"segments": []}})))
    assert await transcriber.transcribe(_AUDIO, _FILENAME) is None


@pytest.mark.asyncio
async def test_request_carries_multipart_file_and_model() -> None:
    captured: dict[str, httpx.Request] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["req"] = request
        return httpx.Response(200, content=orjson.dumps({"text": "ок"}))

    transcriber = _transcriber(handler)
    await transcriber.transcribe(_AUDIO, _FILENAME)

    request = captured["req"]
    assert request.method == "POST"
    assert request.headers["Content-Type"].startswith("multipart/form-data")
    body = request.content
    assert f'name="file"; filename="{_FILENAME}"'.encode() in body
    assert _AUDIO in body
    assert b'name="model"' in body
    assert _MODEL.encode() in body


@pytest.mark.asyncio
async def test_endpoint_is_origin_plus_v1_path() -> None:
    captured: dict[str, httpx.URL] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = request.url
        return httpx.Response(200, content=orjson.dumps({"text": "ок"}))

    transcriber = _transcriber(handler)
    await transcriber.transcribe(_AUDIO, _FILENAME)
    assert str(captured["url"]) == _ENDPOINT


@pytest.mark.asyncio
async def test_trailing_slash_in_base_url_does_not_duplicate_path() -> None:
    captured: dict[str, httpx.URL] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = request.url
        return httpx.Response(200, content=orjson.dumps({"text": "ок"}))

    transcriber = _transcriber(handler, base_url=f"{_ORIGIN}/")
    await transcriber.transcribe(_AUDIO, _FILENAME)
    assert str(captured["url"]) == _ENDPOINT
