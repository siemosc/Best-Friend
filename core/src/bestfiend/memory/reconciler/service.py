"""ReconcilerService — судьба кандидатов в знание относительно уже записанного.

Все кандидаты прогона идут одним батч-вызовом LLM (pin-решение принимает
Reconciler, поэтому кандидат без соседей не может его миновать). Любой сбой —
fail-open в ADD: потерять знание хуже дубля. LLM-вызов выполняется ДО открытия
транзакции персиста (вызывающий применяет готовые решения).
"""

import dataclasses
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

from langfuse import get_client
from loguru import logger

from bestfiend.memory.llm import invoke_structured
from bestfiend.memory.notes.contracts import Note, NoteDraft
from bestfiend.memory.notes.repository import NoteRepository
from bestfiend.memory.reconciler.prompts import build_reconciler_messages
from bestfiend.memory.reconciler.schemas import ReconcileDecision, ReconcileOutput
from bestfiend.memory.settings import MemorySettings


@dataclass(frozen=True, slots=True)
class ReconciledAction:
    """Готовое к исполнению решение по кандидату (uuid вместо индексов)."""

    action: Literal["add", "supersede", "noop", "contradict"]
    # None только у noop — кандидат не вставляется.
    draft: NoteDraft | None
    # Заменяемая/опровергнутая заметка (supersede/contradict); у noop — найденный дубль.
    target_note_id: UUID | None = None


class ReconcilerService:
    """Сверка кандидатов с соседями по памяти и резолв решений LLM."""

    __slots__ = ("_llm_config", "_notes", "_settings")

    def __init__(
        self,
        *,
        notes_repository: NoteRepository,
        settings: MemorySettings,
        llm_config: dict[str, Any],
    ) -> None:
        self._notes = notes_repository
        self._settings = settings
        self._llm_config = llm_config

    async def reconcile(
        self, user_id: UUID, candidates: list[NoteDraft]
    ) -> list[ReconciledAction]:
        """Решения по кандидатам; не бросает — любой сбой даёт ADD-only (fail-open)."""
        if not candidates:
            return []
        with get_client().start_as_current_observation(
            name="memory.reconciler",
            as_type="span",
            input={"candidates": [draft.content for draft in candidates]},
            metadata={"user_id": str(user_id)},
        ) as span:
            try:
                neighbors = [
                    await self._find_neighbors(user_id, draft) for draft in candidates
                ]
                messages = build_reconciler_messages(candidates, neighbors)
                output = await invoke_structured(
                    self._llm_config,
                    ReconcileOutput,
                    messages,
                    user_id=user_id,
                    task="Reconciler",
                )
                if output is None:
                    span.update(output=_actions_payload(_add_only(candidates), True))
                    return _add_only(candidates)
                actions = self._resolve(candidates, neighbors, output)
                span.update(output=_actions_payload(actions, False))
                return actions
            except Exception as exc:  # noqa: BLE001 — фоновый пайплайн не валит процесс
                logger.warning("Reconciler: failed user_id={}: {}", user_id, exc)
                span.update(output=_actions_payload(_add_only(candidates), True))
                return _add_only(candidates)

    async def _find_neighbors(self, user_id: UUID, draft: NoteDraft) -> list[Note]:
        """Соседи кандидата: топ-K по cosine того же kind + заметки с общими сущностями."""
        limit = self._settings.reconciler_neighbors_k
        neighbors: list[Note] = []
        if draft.embedding is not None:
            similar = await self._notes.find_similar(
                user_id, draft.embedding, kinds=[draft.kind], limit=limit
            )
            neighbors.extend(note for note, _ in similar)
        if draft.entity_ids:
            tagged = await self._notes.find_by_entities(
                user_id, list(draft.entity_ids), kinds=[draft.kind], limit=limit
            )
            seen = {note.id for note in neighbors}
            neighbors.extend(note for note in tagged if note.id not in seen)
        return neighbors[:limit]

    def _resolve(
        self,
        candidates: list[NoteDraft],
        neighbors_by_candidate: list[list[Note]],
        output: ReconcileOutput,
    ) -> list[ReconciledAction]:
        """Индексные решения LLM → действия с uuid; невалидное решение → ADD."""
        decision_by_candidate: dict[int, ReconcileDecision] = {}
        for decision in output.decisions:
            if 0 <= decision.candidate_index < len(candidates):
                decision_by_candidate.setdefault(decision.candidate_index, decision)
            else:
                logger.warning(
                    "Reconciler: candidate_index вне диапазона: {}",
                    decision.candidate_index,
                )

        actions: list[ReconciledAction] = []
        for index, draft in enumerate(candidates):
            decision = decision_by_candidate.get(index)
            if decision is None:
                # Кандидат без решения → ADD: потерять знание хуже дубля.
                actions.append(ReconciledAction(action="add", draft=draft))
                continue
            actions.append(
                self._resolve_one(draft, neighbors_by_candidate[index], decision)
            )
        return actions

    def _resolve_one(
        self, draft: NoteDraft, neighbors: list[Note], decision: ReconcileDecision
    ) -> ReconciledAction:
        """Одно решение: применяет pin/наследование, валидирует target."""
        target = self._target_of(decision, neighbors)
        if decision.action == "noop":
            return self._resolve_noop(draft, target, decision)
        if decision.action in ("supersede", "contradict") and target is None:
            # Решение без валидного соседа неисполнимо → fail-open ADD.
            logger.warning(
                "Reconciler: {} без валидного target_index → add", decision.action
            )
            return ReconciledAction(action="add", draft=_pin(draft, decision))
        if decision.action == "supersede" and target is not None:
            return self._resolve_supersede(draft, target, decision)
        if decision.action == "contradict" and target is not None:
            return self._resolve_contradict(draft, target)
        return ReconciledAction(action="add", draft=_pin(draft, decision))

    @staticmethod
    def _resolve_noop(
        draft: NoteDraft,
        target: Note | None,
        decision: ReconcileDecision,
    ) -> ReconciledAction:
        """Преобразует noop-решение в действие."""
        if decision.target_index is not None and target is None:
            # Явный, но невалидный target — решение малоформировано;
            # fail-open в ADD: потерять знание хуже дубля.
            logger.warning("Reconciler: noop с невалидным target_index → add")
            return ReconciledAction(action="add", draft=_pin(draft, decision))
        return ReconciledAction(
            action="noop",
            draft=None,
            target_note_id=target.id if target else None,
        )

    @staticmethod
    def _resolve_supersede(
        draft: NoteDraft,
        target: Note,
        decision: ReconcileDecision,
    ) -> ReconciledAction:
        """Преобразует supersede-решение с наследованием pin."""
        new_draft = _pin(draft, decision)
        # Наследование pin: заменённый pinned-факт не выпадает из профиля молча.
        if decision.pin is False and target.pinned:
            new_draft = dataclasses.replace(
                new_draft, pinned=True, pin_section=target.pin_section
            )
        return ReconciledAction(
            action="supersede", draft=new_draft, target_note_id=target.id
        )

    @staticmethod
    def _resolve_contradict(draft: NoteDraft, target: Note) -> ReconciledAction:
        """Преобразует contradict-решение в конфликтную заметку."""
        # Обе стороны конфликта живы и всплывают в recall с маркером; pin
        # конфликтному знанию не положен.
        contradicted = dataclasses.replace(draft, status="contradicted")
        return ReconciledAction(
            action="contradict", draft=contradicted, target_note_id=target.id
        )

    @staticmethod
    def _target_of(decision: ReconcileDecision, neighbors: list[Note]) -> Note | None:
        """Сосед по target_index решения; вне диапазона → None."""
        if decision.target_index is None:
            return None
        if 0 <= decision.target_index < len(neighbors):
            return neighbors[decision.target_index]
        logger.warning(
            "Reconciler: target_index вне диапазона: {}", decision.target_index
        )
        return None


def _pin(draft: NoteDraft, decision: ReconcileDecision) -> NoteDraft:
    """Применяет pin-решение к драфту."""
    if not decision.pin:
        return draft
    return dataclasses.replace(draft, pinned=True, pin_section=decision.pin_section)


def _add_only(candidates: list[NoteDraft]) -> list[ReconciledAction]:
    """Деградация: все кандидаты вставляются как есть."""
    return [ReconciledAction(action="add", draft=draft) for draft in candidates]


def _actions_payload(
    actions: list[ReconciledAction], fail_open: bool
) -> dict[str, Any]:
    """Output спана reconcile: решения по кандидатам + признак деградации."""
    return {
        "fail_open": fail_open,
        "actions": [
            {
                "action": action.action,
                "content": action.draft.content if action.draft else None,
                "target_note_id": (
                    str(action.target_note_id) if action.target_note_id else None
                ),
            }
            for action in actions
        ],
    }
