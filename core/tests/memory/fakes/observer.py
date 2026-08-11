"""Сборка ObserverService на фейках — общая для тестов observer и journal."""

from typing import Any

from bestfiend.memory.observer.service import ObserverService
from bestfiend.memory.settings import MemorySettings
from tests.memory.fakes.database import TransactionalDatabaseFake
from tests.memory.fakes.entities import EntityRepositoryFake
from tests.memory.fakes.notes import NoteRepositoryFake
from tests.memory.fakes.operation_log import OperationLogRepositoryFake
from tests.memory.fakes.turns import TurnRepositoryFake
from tests.memory.fakes.watermarks import WatermarkRepositoryFake


def build_observer_service(
    *,
    turns: TurnRepositoryFake,
    notes: NoteRepositoryFake,
    entities: EntityRepositoryFake | None = None,
    watermarks: WatermarkRepositoryFake | None = None,
    ops: OperationLogRepositoryFake | None = None,
    settings: MemorySettings | None = None,
    db: TransactionalDatabaseFake | None = None,
    llm_enabled: bool = True,
    reconciler: Any = None,
    reflector: Any = None,
) -> ObserverService:
    """ObserverService на фейках (дефолт: threshold=100, llm включён)."""
    return ObserverService(
        db=db or TransactionalDatabaseFake(),  # type: ignore[arg-type] — фейк по контракту
        turns_repository=turns,  # type: ignore[arg-type]
        notes_repository=notes,  # type: ignore[arg-type]
        entities_repository=entities or EntityRepositoryFake(),  # type: ignore[arg-type]
        watermarks_repository=watermarks or WatermarkRepositoryFake(),  # type: ignore[arg-type]
        ops_repository=ops or OperationLogRepositoryFake(),  # type: ignore[arg-type]
        settings=settings or MemorySettings(observer_token_threshold=100),
        llm_config=(
            {"provider": "openrouter", "model": "stub"} if llm_enabled else None
        ),
        embedder=None,
        reconciler=reconciler,
        reflector=reflector,
    )
