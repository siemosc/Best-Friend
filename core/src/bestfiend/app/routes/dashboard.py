"""HTTP-маршрут состояния зависимостей core."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from bestfiend.app.routes.dependencies import get_runtime, require_session
from bestfiend.control_plane.users.models import UserProfile


def create_dashboard_router() -> APIRouter:
    """Создаёт защищённый маршрут dashboard health."""
    router = APIRouter()

    @router.get("/dashboard/health")
    async def dashboard_health(
        request: Request,
        _user: UserProfile = Depends(require_session),
    ) -> JSONResponse:
        runtime = get_runtime(request)
        snapshot = await runtime.dashboard_service.snapshot()
        return JSONResponse(content=snapshot.model_dump(mode="json"))

    return router
