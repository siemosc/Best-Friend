"""Тестовые двойники журнала операций памяти."""

from typing import Any
from uuid import UUID

from bestfiend.memory.operation_log import MemoryOperation
from tests.memory.fakes.database import is_committed_operation_visible


class OperationLogRepositoryFake:
    """Журнал со staged-записями для каждого executor."""

    def __init__(self) -> None:
        self.staged: list[tuple[Any, list[MemoryOperation]]] = []
        self.log_executors: list[Any] = []

    @property
    def logged(self) -> list[MemoryOperation]:
        """Возвращает операции, видимые после commit."""
        return [
            operation
            for executor, operations in self.staged
            if is_committed_operation_visible(executor)
            for operation in operations
        ]

    def logged_ops(self, operation_name: str) -> list[MemoryOperation]:
        """Возвращает видимые операции одного типа."""
        return [
            operation for operation in self.logged if operation.op == operation_name
        ]

    async def log(
        self,
        user_id: UUID,
        operations: list[MemoryOperation],
        *,
        executor: Any = None,
    ) -> None:
        self.log_executors.append(executor)
        self.staged.append((executor, list(operations)))
