"""HTTP-поверхность OAuth-авторизации пользователей в MCP-серверах.

`create_mcp_oauth_router` — три эндпоинта: старт авторизации (JSON authorization_url),
браузерный callback (302-редиректы на фронт) и отключение (204). Callback переводит
доменные ошибки в query-параметр `?oauth_error=`, а не в JSON: это точка возврата
браузера, не API-вызов. `register_mcp_oauth_exception_handlers` маппит McpOAuthError
в error-контракт для start-эндпоинта (JSON-путь конвенции).
"""

from urllib.parse import urlencode
from uuid import UUID

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response

from bestfiend.app.routes.dependencies import get_runtime, require_session
from bestfiend.app.routes.error_handlers import ErrorResponse
from bestfiend.app.routes.mcp.contracts import OAuthStartResponse
from bestfiend.control_plane.auth.errors import InvalidSessionError
from bestfiend.control_plane.mcp.oauth.errors import McpOAuthError
from bestfiend.control_plane.users.models import UserProfile


_REDIRECT_FOUND = 302


def create_mcp_oauth_router() -> APIRouter:
    """Роутер OAuth-тракта MCP: start (session), browser callback, disconnect (session)."""
    router = APIRouter()

    @router.post(
        "/mcp/subscriptions/{connection_id}/oauth/start",
        response_model=OAuthStartResponse,
    )
    async def start_oauth(
        connection_id: UUID,
        request: Request,
        current: UserProfile = Depends(require_session),
    ) -> JSONResponse:
        """Готовит авторизацию и отдаёт authorization URL для редиректа браузера."""
        svc = get_runtime(request).mcp_oauth_service
        authorization_url = await svc.start_flow(current.user_id, connection_id)
        return JSONResponse(
            content=OAuthStartResponse(
                authorization_url=authorization_url
            ).model_dump(mode="json")
        )

    @router.get("/mcp/oauth/callback")
    async def oauth_callback(
        request: Request,
        state: str,
        code: str | None = None,
        error: str | None = None,
        iss: str | None = None,
    ) -> RedirectResponse:
        """Завершает авторизацию: обмен code на токены и редирект браузера на /mcp.

        Ошибки доменного слоя и явный отказ AS едут во фронт query-параметром
        `?oauth_error=`, успех — `?oauth_connected={name}`. Нет сессии — на /login.
        """
        runtime = get_runtime(request)
        base = runtime.public_base_url
        # Сессия обязательна: без неё некому привязать токены — уводим на логин.
        try:
            user = await require_session(request)
        except InvalidSessionError:
            return RedirectResponse(f"{base}/login", status_code=_REDIRECT_FOUND)
        # Явный отказ юзера/AS (RFC 6749 §4.1.2.1) — код не пришёл.
        if error is not None:
            return _mcp_redirect(base, oauth_error=error)
        if code is None:
            return _mcp_redirect(base, oauth_error="mcp_oauth_missing_code")
        try:
            connection = await runtime.mcp_oauth_service.complete_flow(
                user.user_id, state, code, iss
            )
        except McpOAuthError as exc:
            return _mcp_redirect(base, oauth_error=exc.error_code)
        return _mcp_redirect(base, oauth_connected=connection.name)

    @router.delete("/mcp/subscriptions/{connection_id}/oauth", status_code=204)
    async def disconnect_oauth(
        connection_id: UUID,
        request: Request,
        current: UserProfile = Depends(require_session),
    ) -> Response:
        """Удаляет OAuth-токены (user, connection). Идемпотентно."""
        svc = get_runtime(request).mcp_oauth_service
        await svc.disconnect(current.user_id, connection_id)
        return Response(status_code=204)

    return router


def register_mcp_oauth_exception_handlers(app: FastAPI) -> None:
    """Маппит McpOAuthError (+ подклассы) в error-контракт для JSON-эндпоинтов."""

    @app.exception_handler(McpOAuthError)
    async def _handle_mcp_oauth_error(
        _request: Request,
        exc: McpOAuthError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=ErrorResponse(
                error_code=exc.error_code,
                detail=str(exc),
            ).model_dump(mode="json"),
        )


def _mcp_redirect(base: str, **params: str) -> RedirectResponse:
    """Собирает 302-редирект на /mcp с URL-кодированными query-параметрами."""
    return RedirectResponse(
        f"{base}/mcp?{urlencode(params)}", status_code=_REDIRECT_FOUND
    )
