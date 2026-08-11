"""Write pipeline — записывает ход в лог, триггерит Observer и idle-таймер.

Единственный владелец Observer-триггера: append → maybe_run последовательно
в одной фоновой задаче (вызывается из fire-and-forget persist в GraphRuntime).
Каждый write сбрасывает sleep-таймер: цикл консолидации стартует после
N минут тишины. Сбой Observer/таймера не отменяет запись хода.
"""

from uuid import UUID

from langfuse import get_client
from loguru import logger

from bestfiend.memory.contracts import WriteTurnRequest
from bestfiend.memory.runtime import MemoryRuntime


async def write(
    user_id: UUID,
    request: WriteTurnRequest,
    runtime: MemoryRuntime,
) -> None:
    """Записывает ход в лог, затем триггер Observer и touch sleep-таймера (fail-soft)."""
    with get_client().start_as_current_observation(
        name="memory.write_turn",
        as_type="span",
        # react_loop — счётчиком: полный цикл уже виден в трейсе Graph.invoke.
        input={
            "request_id": request.request_id,
            "user_message": request.user_message,
            "ai_message": request.ai_message,
            "react_loop_messages": len(request.react_loop),
            "token_count_full": request.token_count_full,
            "token_count_loop": request.token_count_loop,
        },
        metadata={"user_id": str(user_id)},
    ) as span:
        await runtime.turns_repository.append_turn(
            user_id=user_id,
            request_id=request.request_id,
            user_message=request.user_message,
            react_loop=request.react_loop,
            ai_message=request.ai_message,
            token_count_full=request.token_count_full,
            token_count_loop=request.token_count_loop,
            created_at=request.created_at,
        )
        span.update(output={"turn_appended": True})
        if runtime.observer is not None:
            try:
                await runtime.observer.maybe_run(user_id)
            except Exception as exc:  # noqa: BLE001 — ход записан, сбой Observer не фатален
                logger.warning(
                    "memory write: observer failed user_id={}: {}", user_id, exc
                )
        if runtime.sleep_scheduler is not None:
            try:
                runtime.sleep_scheduler.touch(user_id)
            except Exception as exc:  # noqa: BLE001 — таймер не критичен для записи
                logger.warning(
                    "memory write: sleep touch failed user_id={}: {}", user_id, exc
                )
