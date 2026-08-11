"""Сборка FastAPI-приложения core и его HTTP-поверхность."""

from fastapi import FastAPI

from bestfiend.app.lifecycle import make_lifespan
from bestfiend.app.routes.artifacts import (
    create_artifacts_router,
    register_artifacts_exception_handlers,
)
from bestfiend.app.routes.assistant import create_assistant_router
from bestfiend.app.routes.auth import create_auth_router
from bestfiend.app.routes.dashboard import create_dashboard_router
from bestfiend.app.routes.error_handlers import (
    register_control_plane_exception_handlers,
)
from bestfiend.app.routes.mcp import (
    create_mcp_oauth_router,
    create_mcp_router,
    register_mcp_exception_handlers,
    register_mcp_oauth_exception_handlers,
)
from bestfiend.app.routes.memory import (
    create_memory_router,
    register_memory_exception_handlers,
)
from bestfiend.app.routes.users import create_users_router
from bestfiend.app.runtime import CoreRuntime


def create_app(runtime: CoreRuntime | None = None) -> FastAPI:
    """Создаёт FastAPI-приложение core. runtime передаётся в тестах (stub)."""
    app = FastAPI(
        title="BestFiend core",
        lifespan=make_lifespan(runtime),
    )
    app.include_router(create_users_router())
    app.include_router(create_auth_router())
    app.include_router(create_assistant_router())
    app.include_router(create_dashboard_router())
    app.include_router(create_artifacts_router())
    app.include_router(create_mcp_router())
    app.include_router(create_mcp_oauth_router())
    app.include_router(create_memory_router())
    register_control_plane_exception_handlers(app)
    register_artifacts_exception_handlers(app)
    register_mcp_exception_handlers(app)
    register_mcp_oauth_exception_handlers(app)
    register_memory_exception_handlers(app)

    @app.get("/health")
    async def health() -> dict[str, str]:
        """Liveness-проба: core поднят."""
        return {"status": "ok"}

    return app


app = create_app()
