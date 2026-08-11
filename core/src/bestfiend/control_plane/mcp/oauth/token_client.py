"""HTTP-клиент token и registration endpoint'ов OAuth-тракта MCP.

Обмен authorization code, refresh, динамическая регистрация клиента (DCR, RFC 7591).
Клиентская аутентификация (client_secret_basic / client_secret_post / none) —
своя реализация: публичного API в mcp 1.27.1 нет. httpx.AsyncClient короткоживущий
per-вызов. Ответы token endpoint парсятся моделями mcp.shared.auth; oauth-ошибка
`invalid_grant` на refresh → McpOAuthRefreshRejectedError, прочие сбои → Exchange/
Registration-ошибки.
"""

import base64
from urllib.parse import quote

import httpx
from mcp.client.auth.utils import handle_token_response_scopes
from mcp.shared.auth import OAuthClientInformationFull, OAuthClientMetadata, OAuthToken
import orjson
from pydantic import AnyUrl, ValidationError

from bestfiend.control_plane.mcp.oauth.errors import (
    McpOAuthExchangeError,
    McpOAuthRefreshRejectedError,
    McpOAuthRegistrationError,
)
from bestfiend.control_plane.mcp.oauth.models import McpOAuthClientRecord


_HTTP_OK = 200
_HTTP_CREATED = 201
_CLIENT_NAME = "BestFiend"
_FORM_HEADERS = {"Content-Type": "application/x-www-form-urlencoded"}


class OAuthTokenClient:
    """Клиент token/registration endpoint'ов authorization server."""

    __slots__ = ("_timeout_s",)

    def __init__(self, *, timeout_s: float) -> None:
        self._timeout_s = timeout_s

    async def exchange_code(
        self,
        *,
        token_endpoint: str,
        code: str,
        code_verifier: str,
        redirect_uri: str,
        resource: str,
        client: McpOAuthClientRecord,
    ) -> OAuthToken:
        """Меняет authorization code на токены (grant_type=authorization_code, PKCE)."""
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": client.client_id,
            "code_verifier": code_verifier,
            "resource": resource,
        }
        return await self._post_token(token_endpoint, data, client, on_refresh=False)

    async def refresh(
        self,
        *,
        token_endpoint: str,
        refresh_token: str,
        resource: str,
        client: McpOAuthClientRecord,
    ) -> OAuthToken:
        """Обновляет токены (grant_type=refresh_token). invalid_grant → Rejected."""
        data = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client.client_id,
            "resource": resource,
        }
        return await self._post_token(token_endpoint, data, client, on_refresh=True)

    async def register_client(
        self,
        *,
        registration_endpoint: str,
        redirect_uri: str,
        scopes: list[str] | None,
    ) -> OAuthClientInformationFull:
        """Регистрирует клиента динамически (DCR, RFC 7591) и возвращает его данные."""
        metadata = OAuthClientMetadata(
            client_name=_CLIENT_NAME,
            redirect_uris=[AnyUrl(redirect_uri)],
            grant_types=["authorization_code", "refresh_token"],
            response_types=["code"],
            scope=" ".join(scopes) if scopes else None,
        )
        payload = metadata.model_dump(by_alias=True, mode="json", exclude_none=True)
        try:
            async with httpx.AsyncClient(timeout=self._timeout_s) as http:
                response = await http.post(
                    registration_endpoint,
                    json=payload,
                    headers={"Content-Type": "application/json"},
                )
        except httpx.HTTPError as exc:
            raise McpOAuthRegistrationError(
                f"Network failure during DCR at {registration_endpoint}: {exc}"
            ) from exc

        if response.status_code not in (_HTTP_OK, _HTTP_CREATED):
            raise McpOAuthRegistrationError(
                f"DCR rejected ({response.status_code}) at {registration_endpoint}"
            )
        try:
            return OAuthClientInformationFull.model_validate_json(response.content)
        except ValidationError as exc:
            raise McpOAuthRegistrationError(
                f"Invalid DCR response from {registration_endpoint}: {exc}"
            ) from exc

    async def _post_token(
        self,
        token_endpoint: str,
        data: dict[str, str],
        client: McpOAuthClientRecord,
        *,
        on_refresh: bool,
    ) -> OAuthToken:
        """POST к token endpoint с клиентской аутентификацией, парсинг ответа."""
        body, headers = _apply_client_auth(data, dict(_FORM_HEADERS), client)
        try:
            async with httpx.AsyncClient(timeout=self._timeout_s) as http:
                response = await http.post(token_endpoint, data=body, headers=headers)
        except httpx.HTTPError as exc:
            raise McpOAuthExchangeError(
                f"Network failure at token endpoint {token_endpoint}: {exc}"
            ) from exc

        if response.status_code == _HTTP_OK:
            try:
                return await handle_token_response_scopes(response)
            except Exception as exc:  # noqa: BLE001 — SDK бросает OAuthTokenError
                raise McpOAuthExchangeError(
                    f"Invalid token response from {token_endpoint}: {exc}"
                ) from exc

        oauth_error = _extract_oauth_error(response.content)
        if on_refresh and oauth_error == "invalid_grant":
            raise McpOAuthRefreshRejectedError(
                f"Refresh rejected by {token_endpoint}: invalid_grant"
            )
        raise McpOAuthExchangeError(
            f"Token endpoint {token_endpoint} rejected request "
            f"({response.status_code}, error={oauth_error})"
        )


def _apply_client_auth(
    data: dict[str, str], headers: dict[str, str], client: McpOAuthClientRecord
) -> tuple[dict[str, str], dict[str, str]]:
    """Накладывает клиентскую аутентификацию по token_endpoint_auth_method."""
    method = client.token_endpoint_auth_method
    if method == "client_secret_basic" and client.client_secret is not None:
        encoded_id = quote(client.client_id, safe="")
        encoded_secret = quote(client.client_secret, safe="")
        credentials = base64.b64encode(
            f"{encoded_id}:{encoded_secret}".encode()
        ).decode()
        headers["Authorization"] = f"Basic {credentials}"
    elif method == "client_secret_post" and client.client_secret is not None:
        data["client_secret"] = client.client_secret
    # method == "none": в body едет только client_id (уже проставлен вызывающим)
    return data, headers


def _extract_oauth_error(content: bytes) -> str | None:
    """Достаёт код `error` из JSON-тела ответа token endpoint (RFC 6749 §5.2)."""
    try:
        payload = orjson.loads(content)
    except orjson.JSONDecodeError:
        return None
    if isinstance(payload, dict):
        error = payload.get("error")
        return error if isinstance(error, str) else None
    return None
