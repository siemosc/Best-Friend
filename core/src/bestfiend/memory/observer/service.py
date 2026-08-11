"""ObserverService — фоновый прогон: лог → заметки журнала и архива.

Триггер — порог необработанных токенов лога после watermark. Конкурентность
гасится общим per-user guard'ом фоновых писателей (core — модульный монолит,
единственный процесс-писатель). Кандидаты в знание проходят Reconciler
(add/supersede/noop/contradict + pin); решения, промоушен профиля и ops-лог
исполняются одной транзакцией с watermark. Консолидация журнала (Reflector →
FIFO-страховка) — отдельная фаза после коммита, под тем же guard'ом. Все сбои
fail-soft: watermark двигается только после успешной записи, упавший прогон
повторится на следующем триггере.
"""

from collections import Counter
import dataclasses
from datetime import UTC, datetime
from typing import Any, Final
from uuid import UUID

from langfuse import get_client
from loguru import logger

from bestfiend.memory.db import MemoryDatabaseClient
from bestfiend.memory.embeddings import MemoryEmbedder, try_embed_documents
from bestfiend.memory.entities.repository import EntityRepository
from bestfiend.memory.journal.budget import apply_journal_budget
from bestfiend.memory.llm import invoke_structured
from bestfiend.memory.locks import MemoryLocks
from bestfiend.memory.notes.contracts import JOURNAL_WEIGHTS, NoteDraft
from bestfiend.memory.notes.profile_budget import apply_profile_budget
from bestfiend.memory.notes.repository import NoteRepository
from bestfiend.memory.observer.prompts import build_observer_messages
from bestfiend.memory.observer.schemas import ObserverOutput
from bestfiend.memory.operation_log import (
    MemoryOperation,
    MemoryOperationLogRepository,
)
from bestfiend.memory.reconciler.service import ReconciledAction, ReconcilerService
from bestfiend.memory.reflector.service import ReflectorService
from bestfiend.memory.settings import MemorySettings
from bestfiend.memory.turns.contracts import Turn
from bestfiend.memory.turns.repository import TurnRepository
from bestfiend.memory.watermarks import OBSERVER_PIPELINE, WatermarkRepository


_DEFAULT_JOURNAL_WEIGHT: Final[int] = JOURNAL_WEIGHTS["mid"]

# Потолок detail в ops-логе noop-решений: след кандидата, не дамп.
_NOOP_DETAIL_MAX_CHARS: Final[int] = 120


class ObserverService:
    """Извлечение заметок из необработанного хвоста лога."""

    __slots__ = (
        "_db",
        "_embedder",
        "_entities",
        "_llm_config",
        "_locks",
        "_notes",
        "_ops",
        "_reconciler",
        "_reflector",
        "_settings",
        "_turns",
        "_watermarks",
    )

    def __init__(
        self,
        *,
        db: MemoryDatabaseClient,
        turns_repository: TurnRepository,
        notes_repository: NoteRepository,
        entities_repository: EntityRepository,
        watermarks_repository: WatermarkRepository,
        ops_repository: MemoryOperationLogRepository,
        settings: MemorySettings,
        llm_config: dict[str, Any] | None,
        embedder: MemoryEmbedder | None,
        locks: MemoryLocks | None = None,
        reconciler: ReconcilerService | None = None,
        reflector: ReflectorService | None = None,
    ) -> None:
        self._db = db
        self._turns = turns_repository
        self._notes = notes_repository
        self._entities = entities_repository
        self._watermarks = watermarks_repository
        self._ops = ops_repository
        self._settings = settings
        self._llm_config = llm_config
        self._embedder = embedder
        self._reconciler = reconciler
        self._reflector = reflector
        # Общий с sleep-time реестр (wiring передаёт один экземпляр на runtime).
        self._locks = locks or MemoryLocks()

    @property
    def is_enabled(self) -> bool:
        """Observer активен, когда LLM-конфиг загружен."""
        return self._llm_config is not None

    async def maybe_run(self, user_id: UUID) -> None:
        """Триггер-чек: запускает прогон при пороге необработанных токенов.

        Конкурентный вызов при идущем прогоне (или sleep-цикле) выходит молча —
        атомарный non-blocking захват, в очередь не встаёт; watermark
        перечитывается под guard'ом — вторая линия идемпотентности.
        """
        if self._llm_config is None:
            return
        async with self._locks.try_hold(user_id) as acquired:
            if not acquired:
                return
            watermark = await self._watermarks.get(user_id, OBSERVER_PIPELINE)
            unprocessed = await self._turns.unprocessed_token_sum(user_id, watermark)
            if unprocessed < self._settings.observer_token_threshold:
                return
            await self._run(user_id, watermark)

    async def _run(self, user_id: UUID, after_id: int) -> None:
        """Один прогон: ходы → LLM → Reconciler → атомарный персист → журнал."""
        turns = await self._turns.turns_after(
            user_id, after_id, self._settings.observer_max_turns
        )
        if not turns:
            return

        with get_client().start_as_current_observation(
            name="memory.observer",
            as_type="span",
            input={
                "after_id": after_id,
                "turns": len(turns),
                "turn_range": f"{turns[0].id}–{turns[-1].id}",
            },
            metadata={"user_id": str(user_id)},
        ) as span:
            known_entities = await self._entities.list_entities(user_id)
            journal = await self._notes.journal_notes(user_id)
            messages = build_observer_messages(
                turns=turns,
                known_entities=known_entities,
                journal_tail=journal,
                now=datetime.now(UTC),
            )

            output = await invoke_structured(
                self._llm_config or {},
                ObserverOutput,
                messages,
                user_id=user_id,
                task="Observer",
            )
            if output is None:
                # Watermark не двигаем — прогон повторится на следующем триггере.
                span.update(output={"llm_failed": True})
                return

            observations, candidates = await self._build_drafts(user_id, output, turns)
            actions = await self._reconcile(user_id, candidates)
            await self._persist_run(
                user_id, observations, actions, last_turn_id=turns[-1].id
            )
            span.update(
                output={
                    "observations": [draft.content for draft in observations],
                    "candidates": [draft.content for draft in candidates],
                    "actions": dict(Counter(action.action for action in actions)),
                }
            )
            await apply_journal_budget(
                user_id,
                db=self._db,
                notes_repository=self._notes,
                ops_repository=self._ops,
                settings=self._settings,
                reflector=self._reflector,
            )
        logger.info(
            "Observer: user_id={} turns={} observations={} candidates={}",
            user_id,
            len(turns),
            len(output.observations),
            len(output.candidates),
        )

    async def _reconcile(
        self, user_id: UUID, candidates: list[NoteDraft]
    ) -> list[ReconciledAction]:
        """Решения по кандидатам; без Reconciler'а — ADD-only."""
        if not candidates:
            return []
        if self._reconciler is None:
            return [ReconciledAction(action="add", draft=draft) for draft in candidates]
        return await self._reconciler.reconcile(user_id, candidates)

    async def _persist_run(
        self,
        user_id: UUID,
        observations: list[NoteDraft],
        actions: list[ReconciledAction],
        *,
        last_turn_id: int,
    ) -> None:
        """Фиксирует прогон атомарно: заметки + решения + профиль + ops + watermark.

        Одна транзакция — сбой на любом шаге откатывает всё, повторный прогон
        не плодит дубли заметок (идемпотентность по watermark). LLM-вызовы
        (Observer/Reconciler) уже сделаны до её открытия.
        """
        async with self._db.transaction() as tx:
            ops: list[MemoryOperation] = []
            observation_ids = await self._notes.insert_notes(
                user_id, observations, executor=tx
            )
            ops.extend(
                MemoryOperation(pipeline="observer", op="add", note_id=note_id)
                for note_id in observation_ids
            )
            for action in actions:
                ops.extend(await self._apply_action(user_id, action, tx))
            demoted = await apply_profile_budget(
                user_id,
                notes_repository=self._notes,
                settings=self._settings,
                executor=tx,
            )
            ops.extend(
                MemoryOperation(pipeline="reconciler", op="demote", note_id=note_id)
                for note_id in demoted
            )
            await self._ops.log(user_id, ops, executor=tx)
            await self._watermarks.advance(
                user_id, OBSERVER_PIPELINE, last_turn_id, executor=tx
            )

    async def _apply_action(
        self, user_id: UUID, action: ReconciledAction, tx: Any
    ) -> list[MemoryOperation]:
        """Исполняет одно решение Reconciler'а в транзакции персиста."""
        if action.action == "noop" or action.draft is None:
            detail = None
            if action.draft is not None:
                detail = action.draft.content[:_NOOP_DETAIL_MAX_CHARS]
            return [
                MemoryOperation(
                    pipeline="reconciler",
                    op="noop",
                    target_note_id=action.target_note_id,
                    detail=detail,
                )
            ]
        [new_id] = await self._notes.insert_notes(user_id, [action.draft], executor=tx)
        if action.action == "supersede" and action.target_note_id is not None:
            await self._notes.supersede(action.target_note_id, new_id, executor=tx)
            return [
                MemoryOperation(
                    pipeline="reconciler",
                    op="supersede",
                    note_id=new_id,
                    target_note_id=action.target_note_id,
                )
            ]
        if action.action == "contradict" and action.target_note_id is not None:
            await self._notes.mark_contradicted(action.target_note_id, executor=tx)
            return [
                MemoryOperation(
                    pipeline="reconciler",
                    op="contradict",
                    note_id=new_id,
                    target_note_id=action.target_note_id,
                )
            ]
        detail = f"pin={action.draft.pin_section}" if action.draft.pinned else None
        return [
            MemoryOperation(
                pipeline="reconciler", op="add", note_id=new_id, detail=detail
            )
        ]

    async def _build_drafts(
        self, user_id: UUID, output: ObserverOutput, turns: list[Turn]
    ) -> tuple[list[NoteDraft], list[NoteDraft]]:
        """Резолвит сущности, векторизует контент; → (наблюдения, кандидаты)."""
        name_to_id = await self._resolve_entities(user_id, output)
        observed_at = datetime.now(UTC)
        span = (turns[0].id, turns[-1].id)

        observations = [
            NoteDraft(
                kind="observation",
                content=obs.content,
                observed_at=observed_at,
                subject=obs.subject,
                event_time=obs.event_time,
                in_journal=True,
                journal_weight=JOURNAL_WEIGHTS.get(obs.weight, _DEFAULT_JOURNAL_WEIGHT),
                source_turn_start=span[0],
                source_turn_end=span[1],
                entity_ids=_ids_for(obs.entities, name_to_id),
            )
            for obs in output.observations
        ]
        candidates = [
            NoteDraft(
                kind=cand.kind,
                content=cand.content,
                observed_at=observed_at,
                # Для preference/rule модельный субъект перепишет инвариант вставки.
                subject=cand.subject,
                event_time=cand.event_time,
                source_turn_start=span[0],
                source_turn_end=span[1],
                entity_ids=_ids_for(cand.entities, name_to_id),
            )
            for cand in output.candidates
        ]
        drafts = [*observations, *candidates]
        if not drafts:
            return [], []

        vectors = await try_embed_documents(
            self._embedder,
            [draft.content for draft in drafts],
            user_id=user_id,
            source="Observer",
        )
        enriched = [
            draft if vector is None else dataclasses.replace(draft, embedding=vector)
            for draft, vector in zip(drafts, vectors, strict=True)
        ]
        return enriched[: len(observations)], enriched[len(observations) :]

    async def _resolve_entities(
        self, user_id: UUID, output: ObserverOutput
    ) -> dict[str, UUID]:
        """Имена из заметок → entity_id; недостающие упомянутые создаются.

        new_entities — подсказка модели; создаются только имена, реально
        присутствующие в тегах заметок (реестр не пухнет от пустых сущностей).
        Ключи результата нормализованы casefold — смешанный регистр тегов
        в одном батче резолвится в одну сущность.
        """
        mentioned = _unique_mentioned_entities(output)
        if not mentioned:
            return {}

        resolved = await self._entities.resolve_names(user_id, mentioned)
        name_to_id = {name.casefold(): eid for name, eid in resolved.items()}
        unresolved = [name for name in mentioned if name.casefold() not in name_to_id]
        for name in unresolved:
            entity_id = await self._create_entity_or_none(user_id, name)
            if entity_id is not None:
                name_to_id[name.casefold()] = entity_id
        return name_to_id

    async def _create_entity_or_none(self, user_id: UUID, name: str) -> UUID | None:
        """Создаёт сущность, не прерывая сохранение заметки при ошибке."""
        try:
            return await self._entities.create_entity(user_id, name)
        except Exception as exc:  # noqa: BLE001 — заметка важнее тега
            logger.warning(
                "Observer: entity create failed name={} user_id={}: {}",
                name,
                user_id,
                exc,
            )
            return None


def _ids_for(names: list[str], name_to_id: dict[str, UUID]) -> tuple[UUID, ...]:
    """Имена тегов заметки → кортеж entity_id (нерезолвленные пропускаются).

    Ключи name_to_id нормализованы casefold — матч не зависит от регистра тега.
    """
    ids: list[UUID] = []
    for name in names:
        entity_id = name_to_id.get(name.strip().casefold())
        if entity_id is not None and entity_id not in ids:
            ids.append(entity_id)
    return tuple(ids)


def _unique_mentioned_entities(output: ObserverOutput) -> list[str]:
    """Собирает уникальные непустые теги заметок без учёта регистра."""
    mentioned: list[str] = []
    seen: set[str] = set()
    note_entities = (
        *(observation.entities for observation in output.observations),
        *(candidate.entities for candidate in output.candidates),
    )
    for names in note_entities:
        for name in names:
            clean_name = name.strip()
            normalized_name = clean_name.casefold()
            if clean_name and normalized_name not in seen:
                seen.add(normalized_name)
                mentioned.append(clean_name)
    return mentioned
