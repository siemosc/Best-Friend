"""Общее для sleep-задач: контекст зависимостей, LLM-вызов, провенанс span'ов.

invoke_structured/try_embed — тонкие адаптеры SleepContext над общими
`memory.llm.invoke_structured` и `memory.embeddings.try_embed` (паттерн живёт
там один раз; здесь — привязка к ctx и префикс «sleep {task}» в логах).
"""

from dataclasses import dataclass
from typing import Any, TypeVar
from uuid import UUID

from langchain_core.messages import BaseMessage
from pydantic import BaseModel

from bestfiend.memory.db import MemoryDatabaseClient
from bestfiend.memory.embeddings import MemoryEmbedder
from bestfiend.memory.embeddings import try_embed as embeddings_try_embed
from bestfiend.memory.entities.repository import EntityRepository
from bestfiend.memory.llm import invoke_structured as llm_invoke_structured
from bestfiend.memory.measurements.repository import MeasurementRepository
from bestfiend.memory.notes.contracts import Note
from bestfiend.memory.notes.repository import NoteRepository
from bestfiend.memory.operation_log import MemoryOperationLogRepository
from bestfiend.memory.settings import MemorySettings


_SchemaT = TypeVar("_SchemaT", bound=BaseModel)


@dataclass(frozen=True, slots=True)
class SleepContext:
    """Зависимости sleep-задач (один набор на цикл)."""

    db: MemoryDatabaseClient
    notes: NoteRepository
    entities: EntityRepository
    ops: MemoryOperationLogRepository
    measurements: MeasurementRepository
    settings: MemorySettings
    llm_config: dict[str, Any]
    embedder: MemoryEmbedder | None


async def invoke_structured(
    ctx: SleepContext,
    schema: type[_SchemaT],
    messages: list[BaseMessage],
    *,
    user_id: UUID,
    task: str,
) -> _SchemaT | None:
    """Structured-вызов LLM sleep-задачи; любой сбой → None (задача скипается, цикл живёт)."""
    return await llm_invoke_structured(
        ctx.llm_config, schema, messages, user_id=user_id, task=f"sleep {task}"
    )


async def try_embed(
    ctx: SleepContext, content: str, *, user_id: UUID, task: str
) -> list[float] | None:
    """Вектор производной заметки; сбой — запись без вектора (FTS её найдёт)."""
    return await embeddings_try_embed(
        ctx.embedder, content, user_id=user_id, source=f"sleep {task}"
    )


def derive_span(sources: list[Note]) -> tuple[int | None, int | None]:
    """Span производной заметки: min/max ходов-источников по ненулевым значениям.

    Правило провенанса Reflector'а для всех производных sleep-заметок —
    цепочка «memory_search → (ходы X–Y) → memory_read_log» не рвётся
    на плотных документах. Все источники без span'а → (None, None).
    """
    starts = [n.source_turn_start for n in sources if n.source_turn_start is not None]
    ends = [n.source_turn_end for n in sources if n.source_turn_end is not None]
    return (min(starts) if starts else None, max(ends) if ends else None)
