"""Сборка HTTP-маршрутов памяти и обработчиков ошибок."""

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse

from bestfiend.app.routes.error_handlers import ErrorResponse
from bestfiend.app.routes.memory.activity import create_activity_router
from bestfiend.app.routes.memory.notes import create_notes_router
from bestfiend.app.routes.memory.overview import create_overview_router
from bestfiend.memory.errors import MemoryDatabaseUnavailableError
from bestfiend.memory.web_facade.errors import MemoryFacadeError


def create_memory_router() -> APIRouter:
    """Собирает все маршруты HTTP API памяти."""
    router = APIRouter()
    router.include_router(create_overview_router())
    router.include_router(create_notes_router())
    router.include_router(create_activity_router())
    return router


def register_memory_exception_handlers(app: FastAPI) -> None:
    """Маппит доменные ошибки памяти в единый HTTP-контракт."""

    @app.exception_handler(MemoryFacadeError)
    async def _handle_memory_api_error(
        _request: Request,
        exc: MemoryFacadeError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=ErrorResponse(
                error_code=exc.error_code,
                detail=str(exc),
            ).model_dump(mode="json"),
        )

    @app.exception_handler(MemoryDatabaseUnavailableError)
    async def _handle_memory_database_unavailable(
        _request: Request,
        exc: MemoryDatabaseUnavailableError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content=ErrorResponse(
                error_code="MEMORY_UNAVAILABLE",
                detail=str(exc),
            ).model_dump(mode="json"),
        )
