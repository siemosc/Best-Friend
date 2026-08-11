"""Подмена structured-вызова LLM в Observer фиксированным выходом."""

from typing import Any

import pytest

from bestfiend.memory.observer.schemas import ObserverOutput
from tests.memory.fakes.database import TransactionalDatabaseFake


def stub_observer_llm(
    monkeypatch: pytest.MonkeyPatch,
    output: ObserverOutput,
    db: TransactionalDatabaseFake | None = None,
) -> list[int]:
    """Подменяет модульный invoke_structured фиксированным выходом; счётчик вызовов.

    Фейк ассертит контракт границы транзакции: LLM-вызов Observer'а никогда
    не живёт внутри открытой транзакции.
    """
    calls: list[int] = []

    async def fake_invoke(*_args: Any, **_kwargs: Any) -> ObserverOutput:
        if db is not None:
            assert db.in_transaction is False
        calls.append(1)
        return output

    monkeypatch.setattr(
        "bestfiend.memory.observer.service.invoke_structured", fake_invoke
    )
    return calls
