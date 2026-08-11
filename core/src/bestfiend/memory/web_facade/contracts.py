"""Контракты HTTP-фасада памяти (зеркалятся в web/src/lib/types.ts)."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from bestfiend.memory.notes.contracts import Note


NOTES_PAGE_LIMIT_DEFAULT = 25
NOTES_PAGE_LIMIT_MAX = 100
SEARCH_LIMIT_MAX = 20

WritableKind = Literal["fact", "preference", "rule"]
SubjectValue = Literal["user", "agent", "world"]
PinSectionValue = Literal["identity", "preferences", "relationships", "rules"]
NoteStatusValue = Literal["active", "superseded", "contradicted"]


class NoteEntityRef(BaseModel):
    """Тег заметки: сущность реестра."""

    id: UUID
    name: str


class NoteView(BaseModel):
    """Заметка для UI: зеркало Note + теги сущностей."""

    id: UUID
    kind: str
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
    entities: list[NoteEntityRef]


class NotesPageResponse(BaseModel):
    """Страница листинга заметок."""

    items: list[NoteView]
    total: int
    limit: int
    offset: int


class NoteSearchResponse(BaseModel):
    """Выдача recall-поиска — то, что увидела бы модель."""

    items: list[NoteView]
    # false — recall-гейт счёл, что уверенного ответа нет (пустой recall — норма).
    gate_passed: bool


class MemoryContextResponse(BaseModel):
    """Постоянный контекст модели: профиль и журнал в порядке промпт-читалок."""

    profile: list[NoteView]
    journal: list[NoteView]


class CreateNoteRequest(BaseModel):
    """Создание заметки руками."""

    kind: WritableKind
    # Для preference/rule модельный субъект перепишет инвариант вставки.
    subject: SubjectValue
    content: str = Field(min_length=1)
    pin: bool = False
    pin_section: PinSectionValue | None = None


class UpdateNoteRequest(BaseModel):
    """PATCH-правка флагов заметки; пропуск поля = «не трогать» (exclude_unset)."""

    pinned: bool | None = None
    pin_section: PinSectionValue | None = None
    in_journal: bool | None = None
    subject: SubjectValue | None = None


class ReviseNoteRequest(BaseModel):
    """Правка контента: supersede-замена с наследованием места знания."""

    content: str = Field(min_length=1)


class EntityView(BaseModel):
    """Сущность реестра с алиасами и числом активных заметок."""

    id: UUID
    canonical_name: str
    aliases: list[str]
    notes_count: int


class MemoryOperationView(BaseModel):
    """Операция ops-лога + клипы контента заметок для читаемости ленты."""

    id: int
    pipeline: str
    op: str
    note_id: UUID | None
    target_note_id: UUID | None
    detail: str | None
    created_at: datetime
    note_content: str | None
    target_note_content: str | None


class OpsPageResponse(BaseModel):
    """Страница ленты операций."""

    items: list[MemoryOperationView]
    total: int
    limit: int
    offset: int


class TurnView(BaseModel):
    """Ход сырого лога в рендере Observer/read_log."""

    id: int
    created_at: datetime
    rendered: str


class TurnsRangeResponse(BaseModel):
    """Диапазон ходов лога (просмотр сцены-источника заметки)."""

    items: list[TurnView]


class MemoryOverviewResponse(BaseModel):
    """Счётчики для шапки вкладки."""

    by_kind: dict[str, int]
    by_subject: dict[str, int]
    by_status: dict[str, int]
    journal_count: int
    pinned_count: int
    entities_count: int


def note_view(note: Note, entities: list[NoteEntityRef]) -> NoteView:
    """Note + теги → NoteView."""
    return NoteView(
        id=note.id,
        kind=note.kind,
        subject=note.subject,
        content=note.content,
        event_time=note.event_time,
        observed_at=note.observed_at,
        status=note.status,
        pinned=note.pinned,
        pin_section=note.pin_section,
        in_journal=note.in_journal,
        journal_weight=note.journal_weight,
        source_turn_start=note.source_turn_start,
        source_turn_end=note.source_turn_end,
        use_count=note.use_count,
        entities=entities,
    )
