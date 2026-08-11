"""Маршруты поиска и управления заметками памяти."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import JSONResponse

from bestfiend.app.routes.memory.dependencies import (
    memory_runtime,
    note_response,
    self_or_admin,
)
from bestfiend.control_plane.users.models import UserProfile
from bestfiend.memory.notes.contracts import NoteKind
from bestfiend.memory.recall.query import recall_notes
from bestfiend.memory.web_facade.contracts import (
    NOTES_PAGE_LIMIT_DEFAULT,
    NOTES_PAGE_LIMIT_MAX,
    SEARCH_LIMIT_MAX,
    CreateNoteRequest,
    NoteSearchResponse,
    NotesPageResponse,
    NoteStatusValue,
    NoteView,
    ReviseNoteRequest,
    SubjectValue,
    UpdateNoteRequest,
)
from bestfiend.memory.web_facade.operations import (
    create_note,
    delete_note,
    revise_note,
    update_note,
)
from bestfiend.memory.web_facade.queries import list_notes, notes_with_refs


# Дефолт повторяемых query-списков задаётся `= None` в сигнатуре —
# default внутри Annotated[..., Query(...)] FastAPI отвергает на старте.
_KINDS_QUERY = Query(description="Фильтр по kind заметки")
_SUBJECTS_QUERY = Query(description="Фильтр по субъекту")
_STATUSES_QUERY = Query(description="Фильтр по статусу")


def create_notes_router() -> APIRouter:
    """Создаёт маршруты листинга, поиска и CRUD заметок."""
    router = APIRouter()

    @router.get(
        "/users/{user_id}/memory/notes",
        response_model=NotesPageResponse,
    )
    async def list_notes_endpoint(
        user_id: UUID,
        request: Request,
        kinds: Annotated[list[NoteKind] | None, _KINDS_QUERY] = None,
        subjects: Annotated[list[SubjectValue] | None, _SUBJECTS_QUERY] = None,
        statuses: Annotated[list[NoteStatusValue] | None, _STATUSES_QUERY] = None,
        pinned: bool | None = None,
        in_journal: bool | None = None,
        entity_id: UUID | None = None,
        q: str | None = None,
        limit: Annotated[
            int,
            Query(ge=1, le=NOTES_PAGE_LIMIT_MAX),
        ] = NOTES_PAGE_LIMIT_DEFAULT,
        offset: Annotated[int, Query(ge=0)] = 0,
        _guard: UserProfile = Depends(self_or_admin),
    ) -> JSONResponse:
        memory = memory_runtime(request)
        notes, total = await list_notes(
            memory.db,
            user_id,
            kinds=list(kinds) if kinds else None,
            subjects=list(subjects) if subjects else None,
            statuses=list(statuses) if statuses else None,
            pinned=pinned,
            in_journal=in_journal,
            entity_id=entity_id,
            q=q,
            limit=limit,
            offset=offset,
        )
        response = NotesPageResponse(
            items=await notes_with_refs(memory.db, notes),
            total=total,
            limit=limit,
            offset=offset,
        )
        return JSONResponse(content=response.model_dump(mode="json"))

    @router.get(
        "/users/{user_id}/memory/notes/search",
        response_model=NoteSearchResponse,
    )
    async def search_notes_endpoint(
        user_id: UUID,
        request: Request,
        q: Annotated[str, Query(min_length=1)],
        kinds: Annotated[list[NoteKind] | None, _KINDS_QUERY] = None,
        subjects: Annotated[list[SubjectValue] | None, _SUBJECTS_QUERY] = None,
        limit: Annotated[int | None, Query(ge=1, le=SEARCH_LIMIT_MAX)] = None,
        _guard: UserProfile = Depends(self_or_admin),
    ) -> JSONResponse:
        memory = memory_runtime(request)
        notes = await recall_notes(
            user_id=user_id,
            query_text=q,
            db=memory.db,
            embedder=memory.embedder,
            entities_repository=memory.entities_repository,
            settings=memory.memory_settings,
            kinds=list(kinds) if kinds else None,
            subjects=list(subjects) if subjects else None,
            top_k=limit,
        )
        response = NoteSearchResponse(
            items=await notes_with_refs(memory.db, notes),
            gate_passed=bool(notes),
        )
        return JSONResponse(content=response.model_dump(mode="json"))

    @router.post(
        "/users/{user_id}/memory/notes",
        response_model=NoteView,
    )
    async def create_note_endpoint(
        user_id: UUID,
        payload: CreateNoteRequest,
        request: Request,
        _guard: UserProfile = Depends(self_or_admin),
    ) -> JSONResponse:
        memory = memory_runtime(request)
        note_id = await create_note(memory, user_id, payload)
        return await note_response(memory, user_id, note_id)

    @router.patch(
        "/users/{user_id}/memory/notes/{note_id}",
        response_model=NoteView,
    )
    async def update_note_endpoint(
        user_id: UUID,
        note_id: UUID,
        payload: UpdateNoteRequest,
        request: Request,
        _guard: UserProfile = Depends(self_or_admin),
    ) -> JSONResponse:
        memory = memory_runtime(request)
        await update_note(memory, user_id, note_id, payload)
        return await note_response(memory, user_id, note_id)

    @router.post(
        "/users/{user_id}/memory/notes/{note_id}/revise",
        response_model=NoteView,
    )
    async def revise_note_endpoint(
        user_id: UUID,
        note_id: UUID,
        payload: ReviseNoteRequest,
        request: Request,
        _guard: UserProfile = Depends(self_or_admin),
    ) -> JSONResponse:
        memory = memory_runtime(request)
        new_note_id = await revise_note(memory, user_id, note_id, payload.content)
        return await note_response(memory, user_id, new_note_id)

    @router.delete(
        "/users/{user_id}/memory/notes/{note_id}",
        status_code=204,
    )
    async def delete_note_endpoint(
        user_id: UUID,
        note_id: UUID,
        request: Request,
        _guard: UserProfile = Depends(self_or_admin),
    ) -> Response:
        memory = memory_runtime(request)
        await delete_note(memory, user_id, note_id)
        return Response(status_code=204)

    return router
