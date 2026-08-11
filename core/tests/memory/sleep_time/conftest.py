"""Сборка SleepContext на общих стабах для тестов sleep-задач."""

from bestfiend.memory.settings import MemorySettings
from bestfiend.memory.sleep_time.context import SleepContext
from tests.memory.fakes import (
    EntityRepositoryFake,
    MeasurementRepositoryFake,
    NoteRepositoryFake,
    OperationLogRepositoryFake,
    TransactionalDatabaseFake,
)


def make_ctx(
    *,
    notes: NoteRepositoryFake | None = None,
    entities: EntityRepositoryFake | None = None,
    ops: OperationLogRepositoryFake | None = None,
    measurements: MeasurementRepositoryFake | None = None,
    db: TransactionalDatabaseFake | None = None,
    settings: MemorySettings | None = None,
) -> SleepContext:
    """SleepContext на стабах (LLM-конфиг фиктивный, вызовы подменяются в тестах)."""
    return SleepContext(
        db=db or TransactionalDatabaseFake(),  # type: ignore[arg-type] — стаб по контракту
        notes=notes or NoteRepositoryFake(),  # type: ignore[arg-type]
        entities=entities or EntityRepositoryFake(),  # type: ignore[arg-type]
        ops=ops or OperationLogRepositoryFake(),  # type: ignore[arg-type]
        measurements=measurements or MeasurementRepositoryFake(),  # type: ignore[arg-type]
        settings=settings or MemorySettings(),
        llm_config={"provider": "openrouter", "model": "stub"},
        embedder=None,
    )
