"""Discovery authorization server для OAuth-тракта MCP.

401-проба MCP URL → WWW-Authenticate → PRM (RFC 9728) → метадата AS (RFC 8414 + OIDC),
на кубиках mcp.client.auth.utils. Любой сетевой или парсинг-сбой транслируется в
McpOAuthDiscoveryError — fail fast, без ретраев.
"""

import httpx
from mcp.client.auth.utils import (
    build_oauth_authorization_server_metadata_discovery_urls,
    build_protected_resource_metadata_discovery_urls,
    extract_resource_metadata_from_www_auth,
    extract_scope_from_www_auth,
    handle_auth_metadata_response,
    handle_protected_resource_response,
)
from mcp.shared.auth import OAuthMetadata, ProtectedResourceMetadata
from mcp.shared.auth_utils import check_resource_allowed, resource_url_from_server_url
from pydantic import BaseModel, ConfigDict

from bestfiend.control_plane.mcp.oauth.errors import McpOAuthDiscoveryError


class AuthorizationServerInfo(BaseModel):
    """Результат discovery: эндпоинты AS и подсказки для сборки authorization URL."""

    model_config = ConfigDict(extra="forbid")

    issuer: str  # из метадаты AS; ожидание для `iss` из callback (RFC 9207)
    authorization_endpoint: str
    token_endpoint: str
    registration_endpoint: str | None = None
    scope_hint: str | None = None  # scope из WWW-Authenticate — высший приоритет
    scopes_supported: list[str] | None = None  # PRM, фолбэк — метадата AS
    code_challenge_methods_supported: list[str] | None = None


async def discover_authorization_server(
    server_url: str, *, timeout_s: float
) -> AuthorizationServerInfo:
    """Резолвит метадату OAuth authorization server для MCP-сервера."""
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            probe = await client.get(server_url)
            www_auth_scope = extract_scope_from_www_auth(probe)
            resource_metadata_url = extract_resource_metadata_from_www_auth(probe)
            prm = await _discover_protected_resource(
                client, server_url, resource_metadata_url
            )
            auth_server_url = (
                str(prm.authorization_servers[0]) if prm is not None else None
            )
            metadata = await _discover_auth_metadata(client, server_url, auth_server_url)
    except httpx.HTTPError as exc:
        raise McpOAuthDiscoveryError(
            f"Network failure during OAuth discovery for {server_url}: {exc}"
        ) from exc

    if metadata is None:
        raise McpOAuthDiscoveryError(
            f"Authorization server metadata not found for {server_url}"
        )

    _verify_resource_match(prm, server_url)

    scopes_supported = _select_scopes_supported(prm, metadata)
    return AuthorizationServerInfo(
        issuer=str(metadata.issuer),
        authorization_endpoint=str(metadata.authorization_endpoint),
        token_endpoint=str(metadata.token_endpoint),
        registration_endpoint=(
            str(metadata.registration_endpoint)
            if metadata.registration_endpoint is not None
            else None
        ),
        scope_hint=www_auth_scope,
        scopes_supported=scopes_supported,
        code_challenge_methods_supported=metadata.code_challenge_methods_supported,
    )


async def _discover_protected_resource(
    client: httpx.AsyncClient, server_url: str, resource_metadata_url: str | None
) -> ProtectedResourceMetadata | None:
    """Перебирает well-known URL PRM (RFC 9728), возвращает первую валидную метадату."""
    urls = build_protected_resource_metadata_discovery_urls(
        resource_metadata_url, server_url
    )
    for url in urls:
        response = await client.get(url)
        prm = await handle_protected_resource_response(response)
        if prm is not None:
            return prm
    return None


async def _discover_auth_metadata(
    client: httpx.AsyncClient, server_url: str, auth_server_url: str | None
) -> OAuthMetadata | None:
    """Перебирает well-known URL метадаты AS (RFC 8414 → OIDC), первую валидную."""
    urls = build_oauth_authorization_server_metadata_discovery_urls(
        auth_server_url, server_url
    )
    for url in urls:
        response = await client.get(url)
        keep_trying, metadata = await handle_auth_metadata_response(response)
        if not keep_trying:
            break
        if metadata is not None:
            return metadata
    return None


def _verify_resource_match(
    prm: ProtectedResourceMetadata | None, server_url: str
) -> None:
    """Mix-up защита: PRM.resource обязан покрывать server_url (RFC 8707/8414)."""
    if prm is None:
        return
    resource = resource_url_from_server_url(server_url)
    if not check_resource_allowed(
        requested_resource=resource, configured_resource=str(prm.resource)
    ):
        raise McpOAuthDiscoveryError(
            f"PRM resource {prm.resource} does not cover server URL {server_url}"
        )


def _select_scopes_supported(
    prm: ProtectedResourceMetadata | None, metadata: OAuthMetadata
) -> list[str] | None:
    """scopes_supported: PRM в приоритете, фолбэк — метадата AS."""
    if prm is not None and prm.scopes_supported is not None:
        return prm.scopes_supported
    return metadata.scopes_supported
