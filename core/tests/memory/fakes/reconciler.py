"""Тестовый двойник reconciler памяти."""

from collections.abc import Callable
from uuid import UUID

from bestfiend.memory.notes.contracts import NoteDraft
from bestfiend.memory.reconciler.service import ReconciledAction


class ReconcilerFake:
    """Reconciler с заданной функцией преобразования кандидатов в действия."""

    def __init__(
        self,
        decide: Callable[[list[NoteDraft]], list[ReconciledAction]],
    ) -> None:
        self.decide = decide
        self.calls: list[list[NoteDraft]] = []

    async def reconcile(
        self,
        user_id: UUID,
        candidates: list[NoteDraft],
    ) -> list[ReconciledAction]:
        self.calls.append(list(candidates))
        return self.decide(candidates)
