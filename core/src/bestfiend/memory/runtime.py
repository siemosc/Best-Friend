"""Runtime container для memory."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

from bestfiend.memory.db import MemoryDatabaseSettings, MemoryPostgreSQLClient
from bestfiend.memory.embeddings import MemoryEmbedder, build_memory_embedder
from bestfiend.memory.entities.repository import EntityRepository
from bestfiend.memory.locks import MemoryLocks
from bestfiend.memory.measurements.repository import MeasurementRepository
from bestfiend.memory.notes.repository import NoteRepository
from bestfiend.memory.observer.service import ObserverService
from bestfiend.memory.operation_log import MemoryOperationLogRepository
from bestfiend.memory.reconciler.service import ReconcilerService
from bestfiend.memory.reflector.service import ReflectorService
from bestfiend.memory.settings import MemorySettings
from bestfiend.memory.sleep_time.probes import ProbeRepository
from bestfiend.memory.sleep_time.scheduler import SleepTimeScheduler
from bestfiend.memory.sleep_time.service import SleepTimeService
from bestfiend.memory.turns.repository import TurnRepository
from bestfiend.memory.watermarks import WatermarkRepository


# Загрузчик config-dict модели по id из таблицы models; None = id не найден.
# Callable вместо репозитория control_plane — memory не зависит от чужой capability.
ModelConfigLoader = Callable[[str], Awaitable[dict[str, Any] | None]]


@dataclass(slots=True)
class MemoryRuntime:
    """Собранный runtime memory."""

    db: MemoryPostgreSQLClient
    turns_repository: TurnRepository
    notes_repository: NoteRepository
    entities_repository: EntityRepository
    watermarks_repository: WatermarkRepository
    ops_repository: MemoryOperationLogRepository
    probes_repository: ProbeRepository
    measurements_repository: MeasurementRepository
    memory_settings: MemorySettings
    model_config_loader: ModelConfigLoader | None = None
    # Заполняются в start(): embedder/observer требуют конфигов моделей из БД.
    embedder: MemoryEmbedder | None = field(default=None, init=False)
    observer: ObserverService | None = field(default=None, init=False)
    sleep_scheduler: SleepTimeScheduler | None = field(default=None, init=False)

    async def start(self) -> None:
        """Подключает DB pool и собирает embedder/observer/sleep (fail-soft по конфигам)."""
        await self.db.connect()
        embedding_config = await self._load_model_config(
            self.memory_settings.memory_embedding_model_id, "embedding"
        )
        if embedding_config is not None:
            try:
                self.embedder = build_memory_embedder(
                    embedding_config, self.memory_settings.memory_embedding_dim
                )
            except Exception as exc:  # noqa: BLE001 — кривой конфиг не валит старт core
                logger.warning(
                    "MemoryRuntime: embedder build failed (recall без векторной ветки): {}",
                    exc,
                )
        llm_config = await self._load_model_config(
            self.memory_settings.memory_llm_model_id, "llm"
        )
        # Один guard на всех фоновых писателей памяти пользователя.
        locks = MemoryLocks()
        # Reconciler/Reflector/sleep живут на той же LLM, что Observer: без
        # конфига Observer выключен целиком, слой качества не собирается.
        reconciler = (
            ReconcilerService(
                notes_repository=self.notes_repository,
                settings=self.memory_settings,
                llm_config=llm_config,
            )
            if llm_config is not None
            else None
        )
        reflector = (
            ReflectorService(
                db=self.db,
                notes_repository=self.notes_repository,
                ops_repository=self.ops_repository,
                llm_config=llm_config,
                embedder=self.embedder,
            )
            if llm_config is not None
            else None
        )
        self.observer = ObserverService(
            db=self.db,
            turns_repository=self.turns_repository,
            notes_repository=self.notes_repository,
            entities_repository=self.entities_repository,
            watermarks_repository=self.watermarks_repository,
            ops_repository=self.ops_repository,
            settings=self.memory_settings,
            llm_config=llm_config,
            embedder=self.embedder,
            locks=locks,
            reconciler=reconciler,
            reflector=reflector,
        )
        if llm_config is not None:
            sleep_service = SleepTimeService(
                db=self.db,
                notes_repository=self.notes_repository,
                entities_repository=self.entities_repository,
                ops_repository=self.ops_repository,
                probes_repository=self.probes_repository,
                measurements_repository=self.measurements_repository,
                settings=self.memory_settings,
                llm_config=llm_config,
                embedder=self.embedder,
                locks=locks,
            )
            self.sleep_scheduler = SleepTimeScheduler(
                sleep_service,
                idle_seconds=self.memory_settings.sleep_idle_minutes * 60,
            )

    async def _load_model_config(
        self, model_id: str, slot: str
    ) -> dict[str, Any] | None:
        """Конфиг модели памяти по id; отсутствие — деградация слота, не ошибка."""
        if self.model_config_loader is None or not model_id:
            logger.info("MemoryRuntime: {} slot выключен (нет id/loader)", slot)
            return None
        try:
            config = await self.model_config_loader(model_id)
        except Exception as exc:  # noqa: BLE001 — память деградирует, не валит старт
            logger.warning(
                "MemoryRuntime: {} config load failed id={}: {}", slot, model_id, exc
            )
            return None
        if config is None:
            logger.warning(
                "MemoryRuntime: {} model id={} отсутствует в models — слот выключен",
                slot,
                model_id,
            )
        return config

    async def stop(self) -> None:
        """Останавливает runtime memory (таймеры sleep гасятся до закрытия пула)."""
        await self.stop_scheduling()
        await self.db.disconnect()

    async def stop_scheduling(self) -> None:
        """Остановить sleep-таймеры без закрытия ресурсов памяти."""
        if self.sleep_scheduler is not None:
            await self.sleep_scheduler.stop()


def create_memory_runtime(
    model_config_loader: ModelConfigLoader | None = None,
) -> MemoryRuntime:
    """Собирает runtime memory (без I/O)."""
    db = MemoryPostgreSQLClient(MemoryDatabaseSettings())
    return MemoryRuntime(
        db=db,
        turns_repository=TurnRepository(db),
        notes_repository=NoteRepository(db),
        entities_repository=EntityRepository(db),
        watermarks_repository=WatermarkRepository(db),
        ops_repository=MemoryOperationLogRepository(db),
        probes_repository=ProbeRepository(db),
        measurements_repository=MeasurementRepository(db),
        memory_settings=MemorySettings(),
        model_config_loader=model_config_loader,
    )
