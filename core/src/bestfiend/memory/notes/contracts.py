"""Контракты слоя заметок и сущностей."""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID


NoteKind = Literal[
    "observation",
    "fact",
    "preference",
    "rule",
    "reflection",
    "entity_card",
    "period_summary",
]
# Субъект, детерминированный самим kind: preference — всегда о пользователе,
# rule — всегда о поведении ассистента. Модели тут доверять нечему.
_SUBJECT_BY_KIND: dict[str, str] = {"preference": "user", "rule": "agent"}
# Kind'ы со свободным субъектом — его определяет модель (Observer / тулза).
_SUBJECT_FREE_KINDS = frozenset({"fact", "observation"})


def resolve_subject(kind: str, subject: str | None) -> str | None:
    """Субъект заметки по инварианту kind; производные агрегаты — без субъекта."""
    fixed = _SUBJECT_BY_KIND.get(kind)
    if fixed is not None:
        return fixed
    if kind in _SUBJECT_FREE_KINDS:
        return subject
    return None


# journal_weight: порядок вытеснения из журнала (лёгкие уходят первыми).
JOURNAL_WEIGHTS: dict[str, int] = {"high": 2, "mid": 1, "low": 0}


@dataclass(frozen=True, slots=True)
class Note:
    """Одна заметка — атом памяти (строка core.notes)."""

    id: UUID
    user_id: UUID
    kind: str
    # None — субъект не применим (производный агрегат) или заметка не классифицирована.
    subject: str | None
    content: str
    event_time: datetime | None
    observed_at: datetime
    status: str
    pinned: bool
    pin_section: str | None
    in_journal: bool
    journal_weight: int
    source_turn_start: int | None
    source_turn_end: int | None
    use_count: int


@dataclass(frozen=True, slots=True)
class NoteDraft:
    """Заметка на запись: контент + флаги + имена сущностей (резолв — на сервисе)."""

    kind: str
    content: str
    observed_at: datetime
    # О ком знание; нормализуется инвариантом kind на границе вставки (repository).
    subject: str | None = None
    event_time: datetime | None = None
    # status != active только у contradict-вставок Reconciler'а (обе стороны конфликта живы).
    status: str = "active"
    in_journal: bool = False
    journal_weight: int = 1
    pinned: bool = False
    pin_section: str | None = None
    source_turn_start: int | None = None
    source_turn_end: int | None = None
    entity_ids: tuple[UUID, ...] = ()
    embedding: list[float] | None = None
