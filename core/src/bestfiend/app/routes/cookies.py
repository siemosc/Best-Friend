"""Session-cookie транспорта: чтение, установка, очистка."""

from uuid import UUID

from fastapi import Request, Response

from bestfiend.control_plane.settings import AuthSettings


def read_session_cookie(request: Request, settings: AuthSettings) -> str | None:
    """Читает session cookie. None если cookie отсутствует."""
    return request.cookies.get(settings.cookie_name)


def set_session_cookie(
    response: Response,
    session_id: UUID,
    settings: AuthSettings,
) -> None:
    """Ставит HttpOnly cookie с session_id."""
    response.set_cookie(
        key=settings.cookie_name,
        value=str(session_id),
        max_age=settings.session_ttl_s,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        path="/",
    )


def clear_session_cookie(response: Response, settings: AuthSettings) -> None:
    """Удаляет session cookie."""
    response.delete_cookie(
        key=settings.cookie_name,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        path="/",
    )
