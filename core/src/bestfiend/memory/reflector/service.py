"""ReflectorService — консолидация журнала: свод строк в reflections.

Двухфазная схема: precompute (LLM-вызов и embeddings — вне транзакции, сетевой
вызов не держит соединение пула) → короткая apply-транзакция (вставка reflections
+ снятие строк с журнала + ops-лог). Любой сбой → False: вызывающий страхуется
FIFO-вытеснением, журнал гарантированно влезает в бюджет.
"""

import dataclasses
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from langfuse import get_client
from loguru import logger

from bestfiend.memory.db import MemoryDatabaseClient
from bestfiend.memory.embeddings import MemoryEmbedder, try_embed_documents
from bestfiend.memory.llm import invoke_structured
from bestfiend.memory.notes.contracts import JOURNAL_WEIGHTS, Note, NoteDraft
from bestfiend.memory.notes.repository import NoteRepository
from bestfiend.memory.operation_log import (
    MemoryOperation,
    MemoryOperationLogRepository,
)
from bestfiend.memory.reflector.prompts import build_reflector_messages
from bestfiend.memory.reflector.schemas import Reflection, ReflectorOutput


@dataclass(frozen=True, slots=True)
class _ReflectorPlan:
    """Результат precompute: что вставить и что снять с журнала."""

    # Драфт reflection + id строк журнала, свёрнутых в него (для ops-провенанса).
    reflections: list[tuple[NoteDraft, list[UUID]]]
    # Строки, уходящие из журнала без свёртки.
    evict_ids: list[UUID]


class ReflectorService:
    """Свод переполненного журнала в плотные reflection-записи."""

    __slots__ = ("_db", "_embedder", "_llm_config", "_notes", "_ops")

    def __init__(
        self,
        *,
        db: MemoryDatabaseClient,
        notes_repository: NoteRepository,
        ops_repository: MemoryOperationLogRepository,
        llm_config: dict[str, Any],
        embedder: MemoryEmbedder | None,
    ) -> None:
        self._db = db
        self._notes = notes_repository
        self._ops = ops_repository
        self._llm_config = llm_config
        self._embedder = embedder

    async def consolidate(self, user_id: UUID, journal: list[Note]) -> bool:
        """Один цикл консолидации; False — план не применён (вызывающий делает FIFO)."""
        with get_client().start_as_current_observation(
            name="memory.reflector",
            as_type="span",
            input={"journal_notes": len(journal)},
            metadata={"user_id": str(user_id)},
        ) as span:
            plan = await self._precompute(user_id, journal)
            if plan is None:
                span.update(output={"applied": False})
                return False
            try:
                await self._apply(user_id, plan)
            except Exception as exc:  # noqa: BLE001 — страховка вызывающего: FIFO
                logger.warning("Reflector: apply failed user_id={}: {}", user_id, exc)
                span.update(output={"applied": False, "apply_failed": True})
                return False
            span.update(
                output={
                    "applied": True,
                    "reflections": [draft.content for draft, _ in plan.reflections],
                    "evicted": len(plan.evict_ids),
                }
            )
        logger.info(
            "Reflector: user_id={} reflections={} evicted={}",
            user_id,
            len(plan.reflections),
            len(plan.evict_ids),
        )
        return True

    async def _precompute(
        self, user_id: UUID, journal: list[Note]
    ) -> _ReflectorPlan | None:
        """Фаза вне транзакции: LLM-вызов, валидация индексов, embeddings."""
        output = await invoke_structured(
            self._llm_config,
            ReflectorOutput,
            build_reflector_messages(journal),
            user_id=user_id,
            task="Reflector",
        )
        if output is None or (not output.reflections and not output.evict_indexes):
            return None

        observed_at = max(note.observed_at for note in journal)
        reflections = [
            _build_reflection(reflection, journal, observed_at)
            for reflection in output.reflections
        ]
        evict_ids = [note.id for note in _notes_at(journal, output.evict_indexes)]
        reflections = await self._embed_reflections(user_id, reflections)
        return _ReflectorPlan(reflections=reflections, evict_ids=evict_ids)

    async def _embed_reflections(
        self,
        user_id: UUID,
        reflections: list[tuple[NoteDraft, list[UUID]]],
    ) -> list[tuple[NoteDraft, list[UUID]]]:
        """Добавляет embeddings к подготовленным reflections."""
        vectors = await try_embed_documents(
            self._embedder,
            [draft.content for draft, _ in reflections],
            user_id=user_id,
            source="Reflector",
        )
        return [
            (
                draft if vector is None else _with_embedding(draft, vector),
                source_ids,
            )
            for (draft, source_ids), vector in zip(reflections, vectors, strict=True)
        ]

    async def _apply(self, user_id: UUID, plan: _ReflectorPlan) -> None:
        """Короткая транзакция: вставка reflections + снятие строк + ops-лог."""
        async with self._db.transaction() as tx:
            ops: list[MemoryOperation] = []
            unjournal_ids: list[UUID] = list(plan.evict_ids)
            for draft, source_ids in plan.reflections:
                [reflection_id] = await self._notes.insert_notes(
                    user_id, [draft], executor=tx
                )
                ops.append(
                    MemoryOperation(
                        pipeline="reflector",
                        op="reflect",
                        note_id=reflection_id,
                        detail=f"свёрнуто строк: {len(source_ids)}",
                    )
                )
                # Свёрнутые строки уходят из журнала со ссылкой на сводную запись.
                ops.extend(
                    MemoryOperation(
                        pipeline="reflector",
                        op="evict",
                        note_id=source_id,
                        target_note_id=reflection_id,
                    )
                    for source_id in source_ids
                )
                unjournal_ids.extend(source_ids)
            ops.extend(
                MemoryOperation(pipeline="reflector", op="evict", note_id=evict_id)
                for evict_id in plan.evict_ids
            )
            await self._notes.evict_from_journal(unjournal_ids, executor=tx)
            await self._ops.log(user_id, ops, executor=tx)


def _notes_at(journal: list[Note], indexes: list[int]) -> list[Note]:
    """Заметки по индексам; вне диапазона — игнор с warning, дубли схлопываются."""
    notes: list[Note] = []
    seen: set[int] = set()
    for index in indexes:
        if not 0 <= index < len(journal):
            logger.warning("Reflector: индекс вне диапазона журнала: {}", index)
            continue
        if index not in seen:
            seen.add(index)
            notes.append(journal[index])
    return notes


def _build_reflection(
    reflection: Reflection,
    journal: list[Note],
    observed_at: datetime,
) -> tuple[NoteDraft, list[UUID]]:
    """Строит reflection-драфт и его provenance."""
    source_notes = _notes_at(journal, reflection.source_indexes)
    starts = [note.source_turn_start for note in source_notes if note.source_turn_start]
    ends = [note.source_turn_end for note in source_notes if note.source_turn_end]
    draft = NoteDraft(
        kind="reflection",
        content=reflection.content,
        observed_at=observed_at,
        in_journal=True,
        journal_weight=JOURNAL_WEIGHTS.get(reflection.weight, 1),
        source_turn_start=min(starts) if starts else None,
        source_turn_end=max(ends) if ends else None,
    )
    return draft, [note.id for note in source_notes]


def _with_embedding(draft: NoteDraft, vector: list[float]) -> NoteDraft:
    """Копия драфта с вектором."""
    return dataclasses.replace(draft, embedding=vector)
