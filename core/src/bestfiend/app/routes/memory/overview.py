"""Маршруты обзора и собранного контекста памяти."""

from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from bestfiend.app.routes.memory.dependencies import (
    memory_runtime,
    self_or_admin,
)
from bestfiend.control_plane.users.models import UserProfile
from bestfiend.memory.web_facade.contracts import (
    MemoryContextResponse,
    MemoryOverviewResponse,
)
from bestfiend.memory.web_facade.queries import (
    memory_context,
    memory_overview,
    notes_with_refs,
)


def create_overview_router() -> APIRouter:
    """Создаёт маршруты обзора и контекста памяти."""
    router = APIRouter()

    @router.get(
        "/users/{user_id}/memory/overview",
        response_model=MemoryOverviewResponse,
    )
    async def memory_overview_endpoint(
        user_id: UUID,
        request: Request,
        _guard: UserProfile = Depends(self_or_admin),
    ) -> JSONResponse:
        memory = memory_runtime(request)
        overview = await memory_overview(memory.db, user_id)
        return JSONResponse(content=overview.model_dump(mode="json"))

    @router.get(
        "/users/{user_id}/memory/context",
        response_model=MemoryContextResponse,
    )
    async def memory_context_endpoint(
        user_id: UUID,
        request: Request,
        _guard: UserProfile = Depends(self_or_admin),
    ) -> JSONResponse:
        memory = memory_runtime(request)
        profile, journal = await memory_context(memory.notes_repository, user_id)
        response = MemoryContextResponse(
            profile=await notes_with_refs(memory.db, profile),
            journal=await notes_with_refs(memory.db, journal),
        )
        return JSONResponse(content=response.model_dump(mode="json"))

    return router
