"""Маршруты сущностей, операций и сырого журнала памяти."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse

from bestfiend.app.routes.memory.dependencies import memory_runtime, self_or_admin
from bestfiend.control_plane.users.models import UserProfile
from bestfiend.memory.operation_log import OpsPipeline
from bestfiend.memory.turns.render import render_turn_for_reader
from bestfiend.memory.web_facade.contracts import (
    EntityView,
    MemoryOperationView,
    OpsPageResponse,
    TurnsRangeResponse,
    TurnView,
)
from bestfiend.memory.web_facade.queries import (
    list_entities_with_counts,
    ops_of_note,
    ops_page,
)


_PIPELINES_QUERY = Query(description="Фильтр по пайплайну операции")


def create_activity_router() -> APIRouter:
    """Создаёт маршруты сущностей, операций и журнала."""
    router = APIRouter()

    @router.get(
        "/users/{user_id}/memory/notes/{note_id}/ops",
        response_model=list[MemoryOperationView],
    )
    async def note_ops_endpoint(
        user_id: UUID,
        note_id: UUID,
        request: Request,
        _guard: UserProfile = Depends(self_or_admin),
    ) -> JSONResponse:
        memory = memory_runtime(request)
        ops = await ops_of_note(memory.db, user_id, note_id)
        return JSONResponse(content=[op.model_dump(mode="json") for op in ops])

    @router.get(
        "/users/{user_id}/memory/entities",
        response_model=list[EntityView],
    )
    async def list_entities_endpoint(
        user_id: UUID,
        request: Request,
        _guard: UserProfile = Depends(self_or_admin),
    ) -> JSONResponse:
        memory = memory_runtime(request)
        entities = await list_entities_with_counts(memory.db, user_id)
        return JSONResponse(
            content=[entity.model_dump(mode="json") for entity in entities]
        )

    @router.get(
        "/users/{user_id}/memory/ops",
        response_model=OpsPageResponse,
    )
    async def ops_page_endpoint(
        user_id: UUID,
        request: Request,
        pipelines: Annotated[list[OpsPipeline] | None, _PIPELINES_QUERY] = None,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
        _guard: UserProfile = Depends(self_or_admin),
    ) -> JSONResponse:
        memory = memory_runtime(request)
        ops, total = await ops_page(
            memory.db,
            user_id,
            pipelines=list(pipelines) if pipelines else None,
            limit=limit,
            offset=offset,
        )
        response = OpsPageResponse(items=ops, total=total, limit=limit, offset=offset)
        return JSONResponse(content=response.model_dump(mode="json"))

    @router.get(
        "/users/{user_id}/memory/turns",
        response_model=TurnsRangeResponse,
    )
    async def turns_range_endpoint(
        user_id: UUID,
        request: Request,
        from_turn: Annotated[int, Query(ge=1)],
        to_turn: Annotated[int, Query(ge=1)],
        _guard: UserProfile = Depends(self_or_admin),
    ) -> JSONResponse:
        memory = memory_runtime(request)
        if to_turn < from_turn:
            from_turn, to_turn = to_turn, from_turn
        turns = await memory.turns_repository.turns_range(
            user_id,
            from_turn,
            to_turn,
            cap=memory.memory_settings.read_log_max_turns,
        )
        response = TurnsRangeResponse(
            items=[
                TurnView(
                    id=turn.id,
                    created_at=turn.created_at,
                    rendered=render_turn_for_reader(turn),
                )
                for turn in turns
            ]
        )
        return JSONResponse(content=response.model_dump(mode="json"))

    return router
