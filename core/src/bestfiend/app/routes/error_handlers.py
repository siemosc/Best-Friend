"""Единый HTTP-контракт ошибок и преобразование доменных ошибок в ответы."""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from bestfiend.control_plane.assistant.errors import AssistantConfigError
from bestfiend.control_plane.auth.errors import AuthError
from bestfiend.control_plane.users.errors import UserError


class ErrorResponse(BaseModel):
    """Стандартный error payload HTTP-поверхности."""

    error_code: str
    detail: str


def register_control_plane_exception_handlers(app: FastAPI) -> None:
    """Регистрирует HTTP-представление ошибок users, auth и assistant."""

    @app.exception_handler(UserError)
    async def _handle_user_error(
        _request: Request,
        exc: UserError,
    ) -> JSONResponse:
        return _error_response(exc.error_code, exc.status_code, str(exc))

    @app.exception_handler(AuthError)
    async def _handle_auth_error(
        _request: Request,
        exc: AuthError,
    ) -> JSONResponse:
        return _error_response(exc.error_code, exc.status_code, str(exc))

    @app.exception_handler(AssistantConfigError)
    async def _handle_assistant_error(
        _request: Request,
        exc: AssistantConfigError,
    ) -> JSONResponse:
        return _error_response(exc.error_code, exc.status_code, str(exc))


def _error_response(error_code: str, status_code: int, detail: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=ErrorResponse(
            error_code=error_code,
            detail=detail,
        ).model_dump(mode="json"),
    )
