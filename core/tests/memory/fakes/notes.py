"""Тестовый двойник репозитория заметок памяти и фабрики его данных."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from bestfiend.memory.notes.contracts import Note, NoteDraft
from tests.memory.fakes.database import (
    is_committed_operation_visible,
    is_operation_visible_to_executor,
)


def make_note(
    content: str,
    *,
    kind: str = "fact",
    subject: str | None = None,
    status: str = "active",
    pinned: bool = False,
    pin_section: str | None = None,
    in_journal: bool = False,
    weight: int = 1,
    use_count: int = 0,
    observed_at: datetime | None = None,
    note_id: UUID | None = None,
) -> Note:
    """Заметка с дефолтами под тесты."""
    return Note(
        id=note_id or uuid4(),
        user_id=uuid4(),
        kind=kind,
        subject=subject,
        content=content,
        event_time=None,
        observed_at=observed_at or datetime(2026, 6, 9, 12, 0, tzinfo=UTC),
        status=status,
        pinned=pinned,
        pin_section=pin_section,
        in_journal=in_journal,
        journal_weight=weight,
        source_turn_start=None,
        source_turn_end=None,
        use_count=use_count,
    )


def make_journal_note(
    content: str,
    *,
    weight: int = 1,
    observed_at: datetime | None = None,
) -> Note:
    """Заметка журнала для тестов вытеснения."""
    return make_note(
        content,
        kind="observation",
        in_journal=True,
        weight=weight,
        observed_at=observed_at,
    )


class NoteRepositoryFake:
    """Заметки: staged-вставки/статусы/демоции, настраиваемые выборки и соседи."""

    def __init__(
        self,
        journal: list[Note] | None = None,
        pinned: list[Note] | None = None,
        *,
        similar: list[tuple[Note, float]] | None = None,
        by_entities: list[Note] | None = None,
        entity_tags: dict[UUID, list[UUID]] | None = None,
        by_id: dict[UUID, Note] | None = None,
    ) -> None:
        self.journal = journal or []
        self.pinned = pinned or []
        self.similar = similar or []
        self.by_entities = by_entities or []
        self.entity_tags = entity_tags or {}
        self.by_id = by_id or {}
        self.stub_user_id = uuid4()
        self.staged_inserts: list[tuple[Any, NoteDraft, UUID]] = []
        self.staged_evictions: list[tuple[Any, list[UUID]]] = []
        self.staged_supersedes: list[tuple[Any, UUID, UUID]] = []
        self.staged_contradicted: list[tuple[Any, UUID]] = []
        self.staged_demotions: list[tuple[Any, list[UUID]]] = []
        self.bumped: list[UUID] = []
        self.bump_fail = False
        self.insert_executors: list[Any] = []
        self.journal_executors: list[Any] = []
        self.evict_executors: list[Any] = []
        self.pinned_executors: list[Any] = []
        self.supersede_executors: list[Any] = []
        self.contradict_executors: list[Any] = []
        self.demote_executors: list[Any] = []
        self.find_similar_calls: list[tuple[list[float], list[str], int]] = []
        self.find_by_entities_calls: list[tuple[list[UUID], list[str], int]] = []
        self.flag_updates: list[dict[str, Any]] = []
        self.flag_update_executors: list[Any] = []
        self.hard_deleted: list[tuple[UUID, UUID]] = []
        self.hard_delete_executors: list[Any] = []
        # ── Конфигурация sleep-выборок (задаётся в тестах после создания) ──
        self.hot_entities: list[UUID] = []
        self.by_entity: dict[UUID, list[Note]] = {}
        self.active_cards: dict[UUID, Note] = {}
        self.observations: list[Note] = []
        self.existing_summaries: dict[datetime, Note] = {}
        self.near_duplicates: list[tuple[Note, Note, float]] = []
        self.recent_sample: list[Note] = []
        self.statuses_map: dict[UUID, str] = {}
        self.statuses_executors: list[Any] = []

    # ── Видимые снаружи проекции (после коммита) ──

    @property
    def inserted(self) -> list[NoteDraft]:
        return [
            draft
            for executor, draft, _ in self.staged_inserts
            if is_committed_operation_visible(executor)
        ]

    @property
    def inserted_with_ids(self) -> list[tuple[NoteDraft, UUID]]:
        return [
            (draft, note_id)
            for executor, draft, note_id in self.staged_inserts
            if is_committed_operation_visible(executor)
        ]

    @property
    def evicted_ids(self) -> list[UUID]:
        return [
            note_id
            for executor, note_ids in self.staged_evictions
            if is_committed_operation_visible(executor)
            for note_id in note_ids
        ]

    @property
    def superseded(self) -> list[tuple[UUID, UUID]]:
        """Пары (старая, новая), видимые после коммита."""
        return [
            (old_note_id, new_note_id)
            for executor, old_note_id, new_note_id in self.staged_supersedes
            if is_committed_operation_visible(executor)
        ]

    @property
    def contradicted_ids(self) -> list[UUID]:
        return [
            note_id
            for executor, note_id in self.staged_contradicted
            if is_committed_operation_visible(executor)
        ]

    @property
    def demoted_ids(self) -> list[UUID]:
        return [
            note_id
            for executor, note_ids in self.staged_demotions
            if is_committed_operation_visible(executor)
            for note_id in note_ids
        ]

    # ── Контракт репозитория ──

    async def insert_notes(
        self, user_id: UUID, drafts: list[NoteDraft], *, executor: Any = None
    ) -> list[UUID]:
        self.insert_executors.append(executor)
        ids: list[UUID] = []
        for draft in drafts:
            note_id = uuid4()
            self.staged_inserts.append((executor, draft, note_id))
            ids.append(note_id)
        return ids

    async def journal_notes(self, user_id: UUID, *, executor: Any = None) -> list[Note]:
        self.journal_executors.append(executor)
        return list(self.journal)

    async def pinned_notes(self, user_id: UUID, *, executor: Any = None) -> list[Note]:
        """Профиль с tx-видимостью: staged pinned-вставки видны в своей транзакции."""
        self.pinned_executors.append(executor)
        demoted = {
            note_id
            for op_executor, ids in self.staged_demotions
            if is_operation_visible_to_executor(op_executor, executor)
            for note_id in ids
        }
        base = [note for note in self.pinned if note.id not in demoted]
        staged = [
            self._note_from_draft(draft, note_id)
            for op_executor, draft, note_id in self.staged_inserts
            if draft.pinned
            and is_operation_visible_to_executor(op_executor, executor)
            and note_id not in demoted
        ]
        return base + staged

    async def evict_from_journal(
        self, note_ids: list[UUID], *, executor: Any = None
    ) -> None:
        self.evict_executors.append(executor)
        self.staged_evictions.append((executor, list(note_ids)))

    async def supersede(
        self, old_note_id: UUID, new_note_id: UUID, *, executor: Any
    ) -> None:
        self.supersede_executors.append(executor)
        self.staged_supersedes.append((executor, old_note_id, new_note_id))

    async def mark_contradicted(self, note_id: UUID, *, executor: Any) -> None:
        self.contradict_executors.append(executor)
        self.staged_contradicted.append((executor, note_id))

    async def demote_from_profile(self, note_ids: list[UUID], *, executor: Any) -> None:
        self.demote_executors.append(executor)
        self.staged_demotions.append((executor, list(note_ids)))

    async def find_similar(
        self,
        user_id: UUID,
        embedding: list[float],
        *,
        kinds: list[str],
        limit: int,
    ) -> list[tuple[Note, float]]:
        self.find_similar_calls.append((embedding, kinds, limit))
        return [(n, s) for n, s in self.similar if n.kind in kinds][:limit]

    async def find_by_entities(
        self,
        user_id: UUID,
        entity_ids: list[UUID],
        *,
        kinds: list[str],
        limit: int,
    ) -> list[Note]:
        self.find_by_entities_calls.append((list(entity_ids), kinds, limit))
        return [n for n in self.by_entities if n.kind in kinds][:limit]

    async def entity_ids_of(self, note_id: UUID, *, executor: Any = None) -> list[UUID]:
        return list(self.entity_tags.get(note_id, []))

    async def note_by_id(
        self, user_id: UUID, note_id: UUID, *, executor: Any = None
    ) -> Note | None:
        note = self.by_id.get(note_id)
        if note is None or note.user_id != user_id:
            return None
        return note

    async def update_note_flags(
        self,
        note_id: UUID,
        user_id: UUID,
        *,
        subject: str | None,
        pinned: bool,
        pin_section: str | None,
        in_journal: bool,
        executor: Any,
    ) -> None:
        self.flag_update_executors.append(executor)
        self.flag_updates.append(
            {
                "note_id": note_id,
                "user_id": user_id,
                "subject": subject,
                "pinned": pinned,
                "pin_section": pin_section,
                "in_journal": in_journal,
            }
        )

    async def hard_delete(self, note_id: UUID, user_id: UUID, *, executor: Any) -> None:
        self.hard_delete_executors.append(executor)
        self.hard_deleted.append((note_id, user_id))

    async def bump_use_count(self, note_ids: list[UUID]) -> None:
        if self.bump_fail:
            raise RuntimeError("bump failed (simulated)")
        self.bumped.extend(note_ids)

    # ── Sleep-выборки (конфигурируемые поля) ──

    async def hot_entities_needing_cards(
        self, user_id: UUID, *, threshold: int, limit: int
    ) -> list[UUID]:
        return self.hot_entities[:limit]

    async def notes_by_entity(
        self, user_id: UUID, entity_id: UUID, *, limit: int
    ) -> list[Note]:
        return self.by_entity.get(entity_id, [])[:limit]

    async def active_card_of(self, user_id: UUID, entity_id: UUID) -> Note | None:
        return self.active_cards.get(entity_id)

    async def observations_in_range(
        self, user_id: UUID, start: datetime, end: datetime
    ) -> list[Note]:
        return [
            note
            for note in self.observations
            if start <= (note.event_time or note.observed_at) < end
        ]

    async def find_summary(self, user_id: UUID, event_time: datetime) -> Note | None:
        return self.existing_summaries.get(event_time)

    async def find_near_duplicates(
        self,
        user_id: UUID,
        *,
        kinds: list[str],
        min_similarity: float,
        limit: int,
    ) -> list[tuple[Note, Note, float]]:
        return [
            (left, right, sim)
            for left, right, sim in self.near_duplicates
            if left.kind in kinds and sim >= min_similarity
        ][:limit]

    async def recent_active_sample(
        self, user_id: UUID, *, since: datetime, limit: int
    ) -> list[Note]:
        return self.recent_sample[:limit]

    async def statuses_of(
        self, note_ids: list[UUID], *, executor: Any
    ) -> dict[UUID, str]:
        self.statuses_executors.append(executor)
        return {
            note_id: self.statuses_map.get(note_id, "active") for note_id in note_ids
        }

    def _note_from_draft(self, draft: NoteDraft, note_id: UUID) -> Note:
        return Note(
            id=note_id,
            user_id=self.stub_user_id,
            kind=draft.kind,
            subject=draft.subject,
            content=draft.content,
            event_time=draft.event_time,
            observed_at=draft.observed_at,
            status=draft.status,
            pinned=draft.pinned,
            pin_section=draft.pin_section,
            in_journal=draft.in_journal,
            journal_weight=draft.journal_weight,
            source_turn_start=draft.source_turn_start,
            source_turn_end=draft.source_turn_end,
            use_count=0,
        )


def note_row(note: Note, **extra: Any) -> dict[str, Any]:
    """Note → dict-строка БД (для стаба fetch)."""
    return {
        "id": note.id,
        "user_id": note.user_id,
        "kind": note.kind,
        "subject": note.subject,
        "content": note.content,
        "event_time": note.event_time,
        "observed_at": note.observed_at,
        "status": note.status,
        "pinned": note.pinned,
        "pin_section": note.pin_section,
        "in_journal": note.in_journal,
        "journal_weight": note.journal_weight,
        "source_turn_start": note.source_turn_start,
        "source_turn_end": note.source_turn_end,
        "use_count": note.use_count,
        **extra,
    }
