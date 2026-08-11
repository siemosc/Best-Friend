"""FastAPI dependencies HTTP-поверхности: доступ к runtime + session-auth guards.

`require_session`, `require_admin`, `require_self_or_admin` (фабрика,
инстанцируется один раз module-level в роутерах).
"""

from collections.abc import Awaitable, Callable
from uuid import UUID

from fastapi import Depends, Request

from bestfiend.app.errors import CoreRuntimeNotInitializedError
from bestfiend.app.routes.cookies import read_session_cookie
from bestfiend.app.runtime import CoreRuntime
from bestfiend.control_plane.auth.errors import ForbiddenError, InvalidSessionError
from bestfiend.control_plane.users.models import UserProfile


def get_runtime(request: Request) -> CoreRuntime:
    """Достаёт core runtime из app.state; падает, если он не поднят."""
    runtime = getattr(request.app.state, "runtime", None)
    if runtime is None:
        raise CoreRuntimeNotInitializedError("core runtime is not initialized")
    return runtime


async def require_session(request: Request) -> UserProfile:
    """Возвращает профиль текущего юзера по session cookie. 401 если сессии нет."""
    runtime = get_runtime(request)
    raw = read_session_cookie(request, runtime.auth_settings)
    if raw is None:
        raise InvalidSessionError("session cookie is missing")
    try:
        session_id = UUID(raw)
    except ValueError as exc:
        raise InvalidSessionError("session cookie is malformed") from exc
    return await runtime.auth_service.resolve_session(session_id)


async def require_admin(
    user: UserProfile = Depends(require_session),
) -> UserProfile:
    """Требует `role == 'admin'`. 401 если нет сессии, 403 если не админ."""
    if user.role != "admin":
        raise ForbiddenError(f"user_id={user.user_id} is not admin")
    return user


def require_self_or_admin(
    param: str = "user_id",
) -> Callable[..., Awaitable[UserProfile]]:
    """Фабрика Depends: доступ если `path[param] == current.user_id` или admin."""

    async def _guard(
        request: Request,
        current: UserProfile = Depends(require_session),
    ) -> UserProfile:
        raw = request.path_params.get(param)
        if raw is None:
            raise ForbiddenError(f"missing path param {param!r}")
        try:
            target_id = UUID(raw)
        except ValueError as exc:
            raise ForbiddenError(f"bad path param {param!r}") from exc
        if current.role == "admin" or current.user_id == target_id:
            return current
        raise ForbiddenError(
            f"user_id={current.user_id} cannot access {param}={target_id}"
        )

    return _guard
