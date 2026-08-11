"""Фабрика MemoryRuntime для тестов тулов памяти."""

from unittest.mock import AsyncMock

from bestfiend.memory.runtime import MemoryRuntime
from bestfiend.memory.settings import MemorySettings
from tests.memory.fakes.database import TransactionalDatabaseFake
from tests.memory.fakes.measurements import MeasurementRepositoryFake
from tests.memory.fakes.notes import NoteRepositoryFake
from tests.memory.fakes.operation_log import OperationLogRepositoryFake


def make_agent_tools_runtime(
    notes: NoteRepositoryFake | None = None,
    ops: OperationLogRepositoryFake | None = None,
    db: TransactionalDatabaseFake | None = None,
    settings: MemorySettings | None = None,
    measurements: MeasurementRepositoryFake | None = None,
) -> MemoryRuntime:
    """MemoryRuntime на фейках: заполнены только тракты, которые трогают тулы."""
    return MemoryRuntime(
        db=db or TransactionalDatabaseFake(),  # type: ignore[arg-type] — фейк по контракту
        turns_repository=AsyncMock(),
        notes_repository=notes or NoteRepositoryFake(),  # type: ignore[arg-type]
        entities_repository=AsyncMock(),
        watermarks_repository=AsyncMock(),
        ops_repository=ops or OperationLogRepositoryFake(),  # type: ignore[arg-type]
        probes_repository=AsyncMock(),
        measurements_repository=measurements or MeasurementRepositoryFake(),  # type: ignore[arg-type]
        memory_settings=settings or MemorySettings(),
        model_config_loader=None,
    )
