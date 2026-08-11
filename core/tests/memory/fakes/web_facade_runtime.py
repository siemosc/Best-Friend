"""Фабрика тестового runtime для веб-фасада памяти."""

from types import SimpleNamespace
from typing import Any

from bestfiend.memory.settings import MemorySettings
from tests.memory.fakes.database import TransactionalDatabaseFake
from tests.memory.fakes.entities import EntityRepositoryFake
from tests.memory.fakes.notes import NoteRepositoryFake
from tests.memory.fakes.operation_log import OperationLogRepositoryFake
from tests.memory.fakes.turns import TurnRepositoryFake


def make_web_facade_memory_runtime(
    *,
    db: Any = None,
    notes: NoteRepositoryFake | None = None,
    operation_log: OperationLogRepositoryFake | None = None,
    entities: EntityRepositoryFake | None = None,
    turns: TurnRepositoryFake | None = None,
    settings: MemorySettings | None = None,
    embedder: Any = None,
) -> Any:
    """Создаёт runtime-фасад с полями, используемыми веб-фасадом памяти."""
    return SimpleNamespace(
        db=db if db is not None else TransactionalDatabaseFake(),
        notes_repository=notes or NoteRepositoryFake(),
        ops_repository=operation_log or OperationLogRepositoryFake(),
        entities_repository=entities or EntityRepositoryFake(),
        turns_repository=turns or TurnRepositoryFake([]),
        memory_settings=settings or MemorySettings(),
        embedder=embedder,
    )
