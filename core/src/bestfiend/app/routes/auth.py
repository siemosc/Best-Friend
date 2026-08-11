"""HTTP-маршруты аутентификации: сессия, привязка канала, смена пароля."""

from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field

from bestfiend.app.routes.cookies import (
    clear_session_cookie,
    read_session_cookie,
    set_session_cookie,
)
from bestfiend.app.routes.dependencies import get_runtime, require_session
from bestfiend.app.routes.user_views import UserResponse, profile_payload
from bestfiend.control_plane.users.models import UserProfile


_LOGIN_PATTERN = r"^[a-zA-Z0-9_-]+$"
_LOGIN_MIN_LEN = 3
_LOGIN_MAX_LEN = 64
_PASSWORD_MIN_LEN = 8
_PASSWORD_MAX_LEN = 256
_BINDING_CODE_LEN = 6
_BINDING_CODE_PATTERN = r"^\d{6}$"


class LoginRequest(BaseModel):
    """Тело POST /auth/login."""

    model_config = ConfigDict(extra="forbid")

    login: str = Field(
        min_length=_LOGIN_MIN_LEN,
        max_length=_LOGIN_MAX_LEN,
        pattern=_LOGIN_PATTERN,
    )
    password: str = Field(min_length=_PASSWORD_MIN_LEN, max_length=_PASSWORD_MAX_LEN)


class BindRequest(BaseModel):
    """Тело POST /auth/bind."""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(
        min_length=_BINDING_CODE_LEN,
        max_length=_BINDING_CODE_LEN,
        pattern=_BINDING_CODE_PATTERN,
    )
    login: str = Field(
        min_length=_LOGIN_MIN_LEN,
        max_length=_LOGIN_MAX_LEN,
        pattern=_LOGIN_PATTERN,
    )
    password: str = Field(min_length=_PASSWORD_MIN_LEN, max_length=_PASSWORD_MAX_LEN)


class ChangePasswordRequest(BaseModel):
    """Тело POST /auth/change-password."""

    model_config = ConfigDict(extra="forbid")

    current_password: str = Field(min_length=1, max_length=_PASSWORD_MAX_LEN)
    new_password: str = Field(
        min_length=_PASSWORD_MIN_LEN, max_length=_PASSWORD_MAX_LEN
    )


def create_auth_router() -> APIRouter:
    """Создаёт маршруты сессии и учётных данных."""
    router = APIRouter()

    @router.get("/auth/me", response_model=UserResponse)
    async def auth_me(
        current_user: UserProfile = Depends(require_session),
    ) -> JSONResponse:
        return JSONResponse(content=profile_payload(current_user))

    @router.post("/auth/login", response_model=UserResponse)
    async def auth_login(payload: LoginRequest, request: Request) -> JSONResponse:
        runtime = get_runtime(request)
        session, user = await runtime.auth_service.login(
            login=payload.login,
            password=payload.password,
        )
        response = JSONResponse(content=profile_payload(user))
        set_session_cookie(response, session.session_id, runtime.auth_settings)
        return response

    @router.post("/auth/bind", response_model=UserResponse)
    async def auth_bind(payload: BindRequest, request: Request) -> JSONResponse:
        runtime = get_runtime(request)
        session, user = await runtime.auth_service.bind_credentials(
            code=payload.code,
            login=payload.login,
            password=payload.password,
        )
        response = JSONResponse(content=profile_payload(user))
        set_session_cookie(response, session.session_id, runtime.auth_settings)
        return response

    @router.post("/auth/logout", status_code=204)
    async def auth_logout(request: Request) -> Response:
        runtime = get_runtime(request)
        raw_session_id = read_session_cookie(request, runtime.auth_settings)
        if raw_session_id is not None:
            try:
                session_id = UUID(raw_session_id)
            except ValueError:
                session_id = None
            if session_id is not None:
                await runtime.auth_service.logout(session_id)
        response = Response(status_code=204)
        clear_session_cookie(response, runtime.auth_settings)
        return response

    @router.post("/auth/change-password", status_code=204)
    async def auth_change_password(
        payload: ChangePasswordRequest,
        request: Request,
        current_user: UserProfile = Depends(require_session),
    ) -> Response:
        runtime = get_runtime(request)
        await runtime.auth_service.change_password(
            user_id=current_user.user_id,
            current_password=payload.current_password,
            new_password=payload.new_password,
        )
        return Response(status_code=204)

    return router
