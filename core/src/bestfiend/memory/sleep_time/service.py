"""SleepTimeService — один цикл консолидации памяти в простое.

Задачи последовательно (карточки → сводки → merge → пробы), каждая fail-soft:
сбой одной не валит следующие. Цикл идёт под общим per-user guard'ом
с Observer (blocking: sleep-time не торопится; Observer при занятом локе
уходит молча — его порог токенов сохраняется до следующего сообщения).
"""

from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

from langfuse import get_client
from loguru import logger

from bestfiend.memory.db import MemoryDatabaseClient
from bestfiend.memory.embeddings import MemoryEmbedder
from bestfiend.memory.entities.repository import EntityRepository
from bestfiend.memory.locks import MemoryLocks
from bestfiend.memory.measurements.repository import MeasurementRepository
from bestfiend.memory.notes.repository import NoteRepository
from bestfiend.memory.operation_log import MemoryOperationLogRepository
from bestfiend.memory.settings import MemorySettings
from bestfiend.memory.sleep_time.context import SleepContext
from bestfiend.memory.sleep_time.duplicate_merge import run_duplicate_merge
from bestfiend.memory.sleep_time.entity_cards import run_entity_cards
from bestfiend.memory.sleep_time.period_summaries import run_period_summaries
from bestfiend.memory.sleep_time.probes import ProbeRepository, run_probes


class SleepTimeService:
    """Цикл sleep-задач для одного пользователя."""

    __slots__ = ("_ctx", "_locks", "_probes")

    def __init__(
        self,
        *,
        db: MemoryDatabaseClient,
        notes_repository: NoteRepository,
        entities_repository: EntityRepository,
        ops_repository: MemoryOperationLogRepository,
        probes_repository: ProbeRepository,
        measurements_repository: MeasurementRepository,
        settings: MemorySettings,
        llm_config: dict[str, Any],
        embedder: MemoryEmbedder | None,
        locks: MemoryLocks,
    ) -> None:
        self._ctx = SleepContext(
            db=db,
            notes=notes_repository,
            entities=entities_repository,
            ops=ops_repository,
            measurements=measurements_repository,
            settings=settings,
            llm_config=llm_config,
            embedder=embedder,
        )
        self._probes = probes_repository
        self._locks = locks

    async def run_cycle(self, user_id: UUID) -> None:
        """Один цикл: каждая задача fail-soft, порядок фиксированный."""
        # Спан внутри lock'а — тайминг чистой работы, без ожидания guard'а.
        async with self._locks.hold(user_id):
            with get_client().start_as_current_observation(
                name="memory.sleep.cycle",
                as_type="span",
                metadata={"user_id": str(user_id)},
            ):
                for task_name, runner in self._tasks():
                    with get_client().start_as_current_observation(
                        name=f"memory.sleep.{task_name}",
                        as_type="span",
                    ) as task_span:
                        try:
                            await runner(user_id)
                        except Exception as exc:  # noqa: BLE001 — задача не валит цикл
                            logger.warning(
                                "sleep cycle: {} failed user_id={}: {}",
                                task_name,
                                user_id,
                                exc,
                            )
                            task_span.update(
                                level="ERROR", status_message=str(exc)[:200]
                            )
        logger.info("sleep cycle: finished user_id={}", user_id)

    def _tasks(self) -> list[tuple[str, Callable[[UUID], Awaitable[None]]]]:
        """Задачи цикла в порядке исполнения."""

        async def probes_task(user_id: UUID) -> None:
            await run_probes(user_id, self._ctx, self._probes)

        return [
            ("cards", lambda user_id: run_entity_cards(user_id, self._ctx)),
            ("summaries", lambda user_id: run_period_summaries(user_id, self._ctx)),
            ("merge", lambda user_id: run_duplicate_merge(user_id, self._ctx)),
            ("probes", probes_task),
        ]
