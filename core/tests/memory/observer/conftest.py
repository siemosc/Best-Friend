"""Фикстуры Observer поверх общих фейков памяти."""

from typing import Any

import pytest

from bestfiend.memory.settings import MemorySettings
from tests.memory.fakes import (
    EntityRepositoryFake,
    NoteRepositoryFake,
    OperationLogRepositoryFake,
    TransactionalDatabaseFake,
    TurnRepositoryFake,
    WatermarkRepositoryFake,
    build_observer_service,
    make_turn,
)


@pytest.fixture
def observer_parts() -> dict[str, Any]:
    """Собранный ObserverService на стабах + доступ к ним (threshold=100)."""
    turns = TurnRepositoryFake([make_turn(1), make_turn(2)])
    notes = NoteRepositoryFake()
    entities = EntityRepositoryFake()
    watermarks = WatermarkRepositoryFake()
    ops = OperationLogRepositoryFake()
    db = TransactionalDatabaseFake()
    settings = MemorySettings(observer_token_threshold=100, journal_token_budget=10_000)
    service = build_observer_service(
        turns=turns,
        notes=notes,
        entities=entities,
        watermarks=watermarks,
        ops=ops,
        settings=settings,
        db=db,
    )
    return {
        "service": service,
        "turns": turns,
        "notes": notes,
        "entities": entities,
        "watermarks": watermarks,
        "ops": ops,
        "db": db,
    }
