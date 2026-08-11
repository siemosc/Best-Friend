"""OAuthTokenClient: три метода клиентской аутентификации, refresh, DCR.

Юнит через httpx.MockTransport. Проверяем состав запроса (form body, заголовки),
парсинг ответа и трансляцию oauth-ошибок в доменные исключения.
"""

import base64
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qs
from uuid import uuid4

import httpx
import orjson
import pytest

from bestfiend.control_plane.mcp.oauth import token_client as token_client_module
from bestfiend.control_plane.mcp.oauth.errors import (
    McpOAuthExchangeError,
    McpOAuthRefreshRejectedError,
    McpOAuthRegistrationError,
)
from bestfiend.control_plane.mcp.oauth.models import McpOAuthClientRecord
from bestfiend.control_plane.mcp.oauth.token_client import OAuthTokenClient


_TOKEN_ENDPOINT = "https://as.example.com/token"
_REGISTRATION_ENDPOINT = "https://as.example.com/register"
_REDIRECT_URI = "https://app.example.com/api/mcp/oauth/callback"
_RESOURCE = "https://mcp.example.com/mcp"
_NOW = datetime.now(UTC)


def _client(
    *,
    method: str = "client_secret_post",
    client_secret: str | None = "s3cret",
) -> McpOAuthClientRecord:
    return McpOAuthClientRecord(
        connection_id=uuid4(),
        client_id="my-client",
        client_secret=client_secret,
        token_endpoint_auth_method=method,
        source="preregistered",
        created_at=_NOW,
    )


def _token_json(**overrides: object) -> bytes:
    base: dict[str, object] = {
        "access_token": "access-xyz",
        "token_type": "Bearer",
        "expires_in": 3600,
        "refresh_token": "refresh-xyz",
        "scope": "openid email",
    }
    base.update(overrides)
    return orjson.dumps(base)


def _install(
    monkeypatch: pytest.MonkeyPatch,
    handler: Callable[[httpx.Request], httpx.Response],
) -> None:
    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient

    def _factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = transport
        return real_client(**kwargs)

    monkeypatch.setattr(token_client_module.httpx, "AsyncClient", _factory)


@pytest.mark.asyncio
async def test_exchange_client_secret_post(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, httpx.Request] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["req"] = request
        return httpx.Response(200, content=_token_json())

    _install(monkeypatch, handler)
    client = OAuthTokenClient(timeout_s=5.0)
    token = await client.exchange_code(
        token_endpoint=_TOKEN_ENDPOINT,
        code="auth-code",
        code_verifier="verifier",
        redirect_uri=_REDIRECT_URI,
        resource=_RESOURCE,
        client=_client(method="client_secret_post"),
    )

    assert token.access_token == "access-xyz"
    assert token.refresh_token == "refresh-xyz"
    body = parse_qs(captured["req"].content.decode())
    assert body["grant_type"] == ["authorization_code"]
    assert body["code"] == ["auth-code"]
    assert body["client_id"] == ["my-client"]
    assert body["client_secret"] == ["s3cret"]  # секрет в body
    assert body["code_verifier"] == ["verifier"]
    assert body["resource"] == [_RESOURCE]
    assert "Authorization" not in captured["req"].headers


@pytest.mark.asyncio
async def test_exchange_client_secret_basic(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, httpx.Request] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["req"] = request
        return httpx.Response(200, content=_token_json())

    _install(monkeypatch, handler)
    client = OAuthTokenClient(timeout_s=5.0)
    await client.exchange_code(
        token_endpoint=_TOKEN_ENDPOINT,
        code="auth-code",
        code_verifier="verifier",
        redirect_uri=_REDIRECT_URI,
        resource=_RESOURCE,
        client=_client(method="client_secret_basic"),
    )

    expected = base64.b64encode(b"my-client:s3cret").decode()
    assert captured["req"].headers["Authorization"] == f"Basic {expected}"
    body = parse_qs(captured["req"].content.decode())
    assert "client_secret" not in body  # при basic секрет не в body


@pytest.mark.asyncio
async def test_exchange_none_method(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, httpx.Request] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["req"] = request
        return httpx.Response(200, content=_token_json())

    _install(monkeypatch, handler)
    client = OAuthTokenClient(timeout_s=5.0)
    await client.exchange_code(
        token_endpoint=_TOKEN_ENDPOINT,
        code="auth-code",
        code_verifier="verifier",
        redirect_uri=_REDIRECT_URI,
        resource=_RESOURCE,
        client=_client(method="none", client_secret=None),
    )

    body = parse_qs(captured["req"].content.decode())
    assert body["client_id"] == ["my-client"]
    assert "client_secret" not in body
    assert "Authorization" not in captured["req"].headers


@pytest.mark.asyncio
async def test_refresh_invalid_grant_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, content=orjson.dumps({"error": "invalid_grant"}))

    _install(monkeypatch, handler)
    client = OAuthTokenClient(timeout_s=5.0)
    with pytest.raises(McpOAuthRefreshRejectedError):
        await client.refresh(
            token_endpoint=_TOKEN_ENDPOINT,
            refresh_token="stale",
            resource=_RESOURCE,
            client=_client(),
        )


@pytest.mark.asyncio
async def test_refresh_other_error_is_exchange_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, content=orjson.dumps({"error": "invalid_client"}))

    _install(monkeypatch, handler)
    client = OAuthTokenClient(timeout_s=5.0)
    with pytest.raises(McpOAuthExchangeError):
        await client.refresh(
            token_endpoint=_TOKEN_ENDPOINT,
            refresh_token="r",
            resource=_RESOURCE,
            client=_client(),
        )


@pytest.mark.asyncio
async def test_exchange_error_on_reject(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, content=orjson.dumps({"error": "invalid_grant"}))

    _install(monkeypatch, handler)
    client = OAuthTokenClient(timeout_s=5.0)
    # invalid_grant на обмене code (не refresh) → ExchangeError, не Rejected
    with pytest.raises(McpOAuthExchangeError):
        await client.exchange_code(
            token_endpoint=_TOKEN_ENDPOINT,
            code="bad",
            code_verifier="verifier",
            redirect_uri=_REDIRECT_URI,
            resource=_RESOURCE,
            client=_client(),
        )


@pytest.mark.asyncio
async def test_register_client_happy(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["payload"] = orjson.loads(request.content)
        return httpx.Response(
            201,
            content=orjson.dumps(
                {
                    "client_id": "dcr-client",
                    "client_secret": "dcr-secret",
                    "token_endpoint_auth_method": "client_secret_post",
                    "redirect_uris": [_REDIRECT_URI],
                }
            ),
        )

    _install(monkeypatch, handler)
    client = OAuthTokenClient(timeout_s=5.0)
    info = await client.register_client(
        registration_endpoint=_REGISTRATION_ENDPOINT,
        redirect_uri=_REDIRECT_URI,
        scopes=["openid", "email"],
    )

    assert info.client_id == "dcr-client"
    assert info.client_secret == "dcr-secret"
    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert payload["client_name"] == "BestFiend"
    assert payload["redirect_uris"] == [_REDIRECT_URI]
    assert payload["scope"] == "openid email"


@pytest.mark.asyncio
async def test_register_client_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, content=b'{"error":"invalid_redirect_uri"}')

    _install(monkeypatch, handler)
    client = OAuthTokenClient(timeout_s=5.0)
    with pytest.raises(McpOAuthRegistrationError):
        await client.register_client(
            registration_endpoint=_REGISTRATION_ENDPOINT,
            redirect_uri=_REDIRECT_URI,
            scopes=None,
        )
