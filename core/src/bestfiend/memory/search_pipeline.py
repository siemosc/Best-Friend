"""Search pipeline — собирает контекст памяти для графа.

Четыре ветки параллельно: хвост лога, журнал, профиль, recall. Каждая ветка
fail-soft: её сбой даёт пустой блок и warning, остальные живут. Хвост лога —
единственная обязательная часть; его сбой обрабатывает вызывающий слой.
"""

import asyncio
from collections.abc import Awaitable
from typing import TypeVar
from uuid import UUID

from langfuse import get_client
from loguru import logger

from bestfiend.memory.budget import ReadBudget
from bestfiend.memory.contracts import MemoryContext
from bestfiend.memory.recall.query import recall_notes
from bestfiend.memory.recall.render import (
    render_journal,
    render_profile,
    render_recall,
)
from bestfiend.memory.runtime import MemoryRuntime
from bestfiend.memory.turns.tail import load_log_tail
from bestfiend.primitives.background_tasks import BackgroundTaskSupervisor


_T = TypeVar("_T")


async def search(
    user_id: UUID,
    runtime: MemoryRuntime,
    current_message: str,
    budget: ReadBudget,
    background_tasks: BackgroundTaskSupervisor,
) -> MemoryContext:
    """Собирает MemoryContext: log_tail + journal + profile + recall по бюджету окна."""
    with get_client().start_as_current_observation(
        name="memory.search",
        as_type="span",
        input={"message": current_message},
        metadata={"user_id": str(user_id)},
    ) as span:
        tail_coro = load_log_tail(
            user_id,
            runtime.turns_repository,
            runtime.memory_settings,
            current_message,
            budget.log_tail,
        )
        journal_coro = _run_branch_fail_soft(
            _render_journal(user_id, runtime), default="", branch="journal"
        )
        profile_coro = _run_branch_fail_soft(
            _render_profile(user_id, runtime), default="", branch="profile"
        )
        recall_coro = _run_branch_fail_soft(
            _render_recall(
                user_id,
                runtime,
                current_message,
                budget.recall,
                background_tasks,
            ),
            default="",
            branch="recall",
        )
        log_tail, journal, profile, recall = await asyncio.gather(
            tail_coro, journal_coro, profile_coro, recall_coro
        )
        # log_tail — счётчиком: его содержимое и так видно в промпте Graph.invoke;
        # текстовые блоки — целиком, это и есть сформированный выход поиска.
        span.update(
            output={
                "log_tail_messages": len(log_tail),
                "journal": journal,
                "profile": profile,
                "recall": recall,
            }
        )
        return MemoryContext(
            log_tail=log_tail, journal=journal, profile=profile, recall=recall
        )


async def _render_journal(user_id: UUID, runtime: MemoryRuntime) -> str:
    """Журнал наблюдений из notes с in_journal."""
    notes = await runtime.notes_repository.journal_notes(user_id)
    return render_journal(notes)


async def _render_profile(user_id: UUID, runtime: MemoryRuntime) -> str:
    """Профиль из pinned-заметок."""
    notes = await runtime.notes_repository.pinned_notes(user_id)
    return render_profile(notes)


async def _render_recall(
    user_id: UUID,
    runtime: MemoryRuntime,
    current_message: str,
    recall_budget: int,
    background_tasks: BackgroundTaskSupervisor,
) -> str:
    """Recall-блок по текущему сообщению (gate внутри recall_notes).

    Вошедшие в блок заметки получают use_count fire-and-forget — сигнал для
    демоции профиля и будущей ревизии; сбой инкремента не трогает ответ.
    """
    notes = await recall_notes(
        user_id=user_id,
        query_text=current_message,
        db=runtime.db,
        embedder=runtime.embedder,
        entities_repository=runtime.entities_repository,
        settings=runtime.memory_settings,
        recall_budget=recall_budget,
    )
    if notes:
        background_tasks.create_task(
            runtime.notes_repository.bump_use_count([note.id for note in notes]),
            name=f"memory-use-count:{user_id}",
        )
    return render_recall(notes)


async def _run_branch_fail_soft(coro: Awaitable[_T], *, default: _T, branch: str) -> _T:
    """Fail-soft обёртка ветки: исключение → default + warning."""
    try:
        return await coro
    except Exception as exc:  # noqa: BLE001 — ветка памяти не валит сборку контекста
        logger.warning("memory search: {} branch failed: {}", branch, exc)
        return default
