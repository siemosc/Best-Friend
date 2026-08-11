"""Тестовые реализации контрактов памяти, сгруппированные по production-модулям."""

from tests.memory.fakes.agent_tools_runtime import make_agent_tools_runtime
from tests.memory.fakes.database import TransactionalDatabaseFake, TransactionBuffer
from tests.memory.fakes.entities import EntityRepositoryFake
from tests.memory.fakes.measurements import (
    MeasurementRepositoryFake,
    make_metric_aggregate,
)
from tests.memory.fakes.notes import (
    NoteRepositoryFake,
    make_journal_note,
    make_note,
    note_row,
)
from tests.memory.fakes.observer import build_observer_service
from tests.memory.fakes.observer_llm import stub_observer_llm
from tests.memory.fakes.operation_log import OperationLogRepositoryFake
from tests.memory.fakes.reconciler import ReconcilerFake
from tests.memory.fakes.reflector import ReflectorFake
from tests.memory.fakes.turns import TurnRepositoryFake, make_turn
from tests.memory.fakes.watermarks import WatermarkRepositoryFake
from tests.memory.fakes.web_facade_runtime import make_web_facade_memory_runtime


__all__ = [
    "EntityRepositoryFake",
    "MeasurementRepositoryFake",
    "NoteRepositoryFake",
    "OperationLogRepositoryFake",
    "ReconcilerFake",
    "ReflectorFake",
    "TransactionBuffer",
    "TransactionalDatabaseFake",
    "TurnRepositoryFake",
    "WatermarkRepositoryFake",
    "build_observer_service",
    "stub_observer_llm",
    "make_agent_tools_runtime",
    "make_web_facade_memory_runtime",
    "make_journal_note",
    "make_metric_aggregate",
    "make_note",
    "make_turn",
    "note_row",
]
