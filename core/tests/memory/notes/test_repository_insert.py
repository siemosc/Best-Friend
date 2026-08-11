"""Граница вставки заметок: нормализация subject инвариантом kind.

Единственный шов, через который пишут все писатели (Observer, тулзы,
sleep-time merge) — невалидный субъект не может попасть в БД мимо него.
"""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from bestfiend.memory.notes.contracts import NoteDraft, resolve_subject
from bestfiend.memory.notes.repository import NoteRepository


@pytest.mark.parametrize(
    ("kind", "given", "expected"),
    [
        ("preference", None, "user"),  # детерминирован kind'ом
        ("preference", "world", "user"),  # модельный мусор переписан
        ("rule", None, "agent"),
        ("rule", "user", "agent"),
        ("fact", "world", "world"),  # свободный — модельный субъект как есть
        ("fact", None, None),
        ("observation", "agent", "agent"),
        ("reflection", "user", None),  # производные агрегаты — без субъекта
        ("entity_card", "user", None),
        ("period_summary", "user", None),
    ],
)
def test_resolve_subject_invariant(
    kind: str, given: str | None, expected: str | None
) -> None:
    """Инвариант субъекта: фиксированный из kind, свободный модельный, производный None."""
    assert resolve_subject(kind, given) == expected


class FakeExecutor:
    """Собирает execute-вызовы (SQL + параметры) без исполнения."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    async def execute(self, query: str, *args: object) -> str:
        self.calls.append((query, args))
        return "INSERT 0 1"

    async def fetch(self, query: str, *args: object) -> list[object]:
        raise AssertionError("вставка не читает")

    async def fetch_one(self, query: str, *args: object) -> object | None:
        raise AssertionError("вставка не читает")


@pytest.mark.asyncio
async def test_insert_normalizes_subject_at_boundary() -> None:
    """INSERT-параметр subject нормализован для любого драфта любого писателя."""
    executor = FakeExecutor()
    repository = NoteRepository(db=object())  # type: ignore[arg-type] — путь executor
    observed_at = datetime(2026, 6, 10, 12, 0, tzinfo=UTC)
    drafts = [
        NoteDraft(kind="preference", content="любит чай", observed_at=observed_at),
        NoteDraft(
            kind="rule",
            content="отвечать кратко",
            observed_at=observed_at,
            subject="user",
        ),
        NoteDraft(
            kind="fact",
            content="сервер в подвале",
            observed_at=observed_at,
            subject="world",
        ),
        NoteDraft(
            kind="entity_card",
            content="досье",
            observed_at=observed_at,
            subject="user",
        ),
    ]

    await repository.insert_notes(uuid4(), drafts, executor=executor)

    inserts = [args for sql, args in executor.calls if "INSERT INTO notes" in sql]
    # Порядок параметров _INSERT_NOTE_SQL: $3 kind, $4 subject.
    assert [(args[2], args[3]) for args in inserts] == [
        ("preference", "user"),
        ("rule", "agent"),
        ("fact", "world"),
        ("entity_card", None),
    ]
