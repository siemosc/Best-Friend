"""Discovery authorization server: happy-path, фолбэк без WWW-Authenticate, mix-up.

Юнит через httpx.MockTransport — реальной сети нет. Транспорт подменяется в
AsyncClient модуля discovery, маршрутизация ответов по URL запроса.
"""

from collections.abc import Callable
from typing import Any

import httpx
import orjson
import pytest

from bestfiend.control_plane.mcp.oauth import discovery
from bestfiend.control_plane.mcp.oauth.errors import McpOAuthDiscoveryError


_SERVER_URL = "https://mcp.example.com/mcp"
_PRM_WWW_URL = "https://mcp.example.com/.well-known/oauth-protected-resource/mcp"
_AS_METADATA_URL = "https://as.example.com/.well-known/oauth-authorization-server"


def _json_response(payload: dict[str, object]) -> httpx.Response:
    return httpx.Response(200, content=orjson.dumps(payload))


def _as_metadata(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "issuer": "https://as.example.com",
        "authorization_endpoint": "https://as.example.com/authorize",
        "token_endpoint": "https://as.example.com/token",
        "registration_endpoint": "https://as.example.com/register",
        "scopes_supported": ["openid", "email"],
        "code_challenge_methods_supported": ["S256"],
    }
    base.update(overrides)
    return base


def _prm(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "resource": "https://mcp.example.com/mcp",
        "authorization_servers": ["https://as.example.com"],
        "scopes_supported": ["openid", "email", "profile"],
    }
    base.update(overrides)
    return base


def _install_transport(
    monkeypatch: pytest.MonkeyPatch,
    handler: Callable[[httpx.Request], httpx.Response],
) -> None:
    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient

    def _factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = transport
        return real_client(**kwargs)

    monkeypatch.setattr(discovery.httpx, "AsyncClient", _factory)


@pytest.mark.asyncio
async def test_discovery_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == _SERVER_URL:
            return httpx.Response(
                401,
                headers={
                    "WWW-Authenticate": (
                        f'Bearer resource_metadata="{_PRM_WWW_URL}", '
                        'scope="openid email"'
                    )
                },
            )
        if url == _PRM_WWW_URL:
            return _json_response(_prm())
        if url == _AS_METADATA_URL:
            return _json_response(_as_metadata())
        return httpx.Response(404)

    _install_transport(monkeypatch, handler)

    info = await discovery.discover_authorization_server(_SERVER_URL, timeout_s=5.0)

    # pydantic AnyHttpUrl нормализует bare-host issuer с хвостовым слэшем
    assert info.issuer == "https://as.example.com/"
    assert info.authorization_endpoint == "https://as.example.com/authorize"
    assert info.token_endpoint == "https://as.example.com/token"
    assert info.registration_endpoint == "https://as.example.com/register"
    assert info.scope_hint == "openid email"
    # scopes_supported: PRM в приоритете над метадатой AS
    assert info.scopes_supported == ["openid", "email", "profile"]
    assert info.code_challenge_methods_supported == ["S256"]


@pytest.mark.asyncio
async def test_discovery_without_www_authenticate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == _SERVER_URL:
            return httpx.Response(401)  # 401 без WWW-Authenticate
        if url == _PRM_WWW_URL:  # путь-well-known фолбэк
            return _json_response(_prm())
        if url == _AS_METADATA_URL:
            return _json_response(_as_metadata())
        return httpx.Response(404)

    _install_transport(monkeypatch, handler)

    info = await discovery.discover_authorization_server(_SERVER_URL, timeout_s=5.0)

    assert info.scope_hint is None
    assert info.scopes_supported == ["openid", "email", "profile"]
    assert info.token_endpoint == "https://as.example.com/token"


@pytest.mark.asyncio
async def test_discovery_mixup_foreign_resource(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == _SERVER_URL:
            return httpx.Response(
                401,
                headers={
                    "WWW-Authenticate": f'Bearer resource_metadata="{_PRM_WWW_URL}"'
                },
            )
        if url == _PRM_WWW_URL:
            return _json_response(_prm(resource="https://evil.example.com/mcp"))
        if url == _AS_METADATA_URL:
            return _json_response(_as_metadata())
        return httpx.Response(404)

    _install_transport(monkeypatch, handler)

    with pytest.raises(McpOAuthDiscoveryError):
        await discovery.discover_authorization_server(_SERVER_URL, timeout_s=5.0)


@pytest.mark.asyncio
async def test_discovery_metadata_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == _SERVER_URL:
            return httpx.Response(
                401,
                headers={
                    "WWW-Authenticate": f'Bearer resource_metadata="{_PRM_WWW_URL}"'
                },
            )
        if url == _PRM_WWW_URL:
            return _json_response(_prm())
        return httpx.Response(404)  # метадата AS не найдена

    _install_transport(monkeypatch, handler)

    with pytest.raises(McpOAuthDiscoveryError):
        await discovery.discover_authorization_server(_SERVER_URL, timeout_s=5.0)


@pytest.mark.asyncio
async def test_discovery_network_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    _install_transport(monkeypatch, handler)

    with pytest.raises(McpOAuthDiscoveryError):
        await discovery.discover_authorization_server(_SERVER_URL, timeout_s=5.0)
