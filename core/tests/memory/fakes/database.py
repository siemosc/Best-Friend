"""Транзакционные тестовые двойники базы памяти."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any


class TransactionBuffer:
    """Маркер транзакции: операции видны после commit."""

    def __init__(self) -> None:
        self.committed = False
        self.rolled_back = False


class TransactionalDatabaseFake:
    """База, выдающая буфер транзакции и фиксирующая commit или rollback."""

    def __init__(self) -> None:
        self.transactions: list[TransactionBuffer] = []
        self._depth = 0

    @property
    def in_transaction(self) -> bool:
        """Открыта ли транзакция сейчас."""
        return self._depth > 0

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[TransactionBuffer]:
        transaction = TransactionBuffer()
        self.transactions.append(transaction)
        self._depth += 1
        try:
            yield transaction
        except Exception:
            transaction.rolled_back = True
            raise
        else:
            transaction.committed = True
        finally:
            self._depth -= 1


def is_committed_operation_visible(executor: Any) -> bool:
    """Операция видна снаружи, если выполнена без транзакции или закоммичена."""
    return executor is None or (
        isinstance(executor, TransactionBuffer) and executor.committed
    )


def is_operation_visible_to_executor(
    operation_executor: Any,
    current_executor: Any,
) -> bool:
    """Операция видна своей транзакции либо после commit."""
    if operation_executor is not None and operation_executor is current_executor:
        return True
    return is_committed_operation_visible(operation_executor)
