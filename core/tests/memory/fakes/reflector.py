"""Тестовый двойник reflector памяти."""

from collections.abc import Callable
from uuid import UUID

from bestfiend.memory.notes.contracts import Note


class ReflectorFake:
    """Reflector с заданным исходом и опциональным побочным эффектом."""

    def __init__(
        self,
        *,
        applied: bool,
        on_consolidate: Callable[[], None] | None = None,
    ) -> None:
        self.applied = applied
        self.on_consolidate = on_consolidate
        self.calls: list[list[Note]] = []

    async def consolidate(self, user_id: UUID, journal: list[Note]) -> bool:
        self.calls.append(list(journal))
        if self.applied and self.on_consolidate is not None:
            self.on_consolidate()
        return self.applied
