"""Общие зависимости и сериализация ответов HTTP-маршрутов памяти."""

from uuid import UUID

from fastapi import Request
from fastapi.responses import JSONResponse

from bestfiend.app.routes.dependencies import get_runtime, require_self_or_admin
from bestfiend.memory.runtime import MemoryRuntime
from bestfiend.memory.web_facade.queries import note_view_by_id


self_or_admin = require_self_or_admin()


def memory_runtime(request: Request) -> MemoryRuntime:
    """Возвращает MemoryRuntime текущего приложения."""
    return get_runtime(request).memory_runtime


async def note_response(
    memory: MemoryRuntime,
    user_id: UUID,
    note_id: UUID,
) -> JSONResponse:
    """Отдаёт свежее состояние заметки после операции."""
    view = await note_view_by_id(memory.db, memory.notes_repository, user_id, note_id)
    return JSONResponse(content=view.model_dump(mode="json"))
