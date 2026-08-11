"""Исполнение memory tools для одного пользователя."""

from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

from bestfiend.memory.embeddings import try_embed
from bestfiend.memory.measurements.contracts import (
    MeasurementBucket,
    MeasurementDraft,
    normalize_metric_name,
)
from bestfiend.memory.measurements.render import render_aggregates
from bestfiend.memory.notes.contracts import NoteDraft
from bestfiend.memory.notes.write_service import (
    insert_note_with_ops,
    revise_with_inheritance,
)
from bestfiend.memory.recall.query import recall_notes, resolve_note_by_statement
from bestfiend.memory.recall.render import render_note_line_with_span
from bestfiend.memory.runtime import MemoryRuntime
from bestfiend.memory.turns.render import render_turn_for_reader


class MemoryToolHandlers:
    """Обработчики memory tools с привязкой к runtime и пользователю."""

    def __init__(self, runtime: MemoryRuntime, user_id: UUID) -> None:
        self._runtime = runtime
        self._user_id = user_id

    async def search(
        self,
        query: str,
        kinds: list[str] | None = None,
        subjects: list[str] | None = None,
        limit: int | None = None,
    ) -> str:
        """Ищет заметки с фильтрацией до ранжирования."""
        runtime = self._runtime
        notes = await recall_notes(
            user_id=self._user_id,
            query_text=query,
            db=runtime.db,
            embedder=runtime.embedder,
            entities_repository=runtime.entities_repository,
            settings=runtime.memory_settings,
            kinds=kinds,
            subjects=subjects,
            top_k=limit,
        )
        if not notes:
            return (
                "Ничего не найдено. Попробуй другую формулировку: имена, "
                "конкретные термины, синонимы."
            )
        # Span ходов-источников — мост к memory_read_log (дословная сцена).
        lines = "\n".join(f"- {render_note_line_with_span(note)}" for note in notes)
        return f"Найдено в памяти:\n{lines}"

    async def save(
        self,
        content: str,
        kind: Literal["fact", "preference", "rule"],
        subject: Literal["user", "agent", "world"],
        pin: bool = False,
        pin_section: str | None = None,
    ) -> str:
        """Сохраняет новую заметку и запись в журнале операций."""
        runtime = self._runtime
        # Для preference/rule модельный субъект перепишет инвариант вставки.
        draft = NoteDraft(
            kind=kind,
            content=content,
            observed_at=datetime.now(UTC),
            subject=subject,
            pinned=pin,
            pin_section=pin_section if pin else None,
            embedding=await try_embed(
                runtime.embedder,
                content,
                user_id=self._user_id,
                source="memory tool",
            ),
        )
        await insert_note_with_ops(runtime, self._user_id, draft, pipeline="tool")
        target = "профиль" if pin else "архив"
        return f"Запомнил ({target}): {content}"

    async def revise(
        self,
        statement_to_replace: str,
        corrected_statement: str,
        kind: Literal["fact", "preference", "rule"] = "fact",
        subject: Literal["user", "agent", "world"] | None = None,
    ) -> str:
        """Исправляет найденную заметку или создаёт новую."""
        runtime = self._runtime
        target = await resolve_note_by_statement(
            user_id=self._user_id,
            statement=statement_to_replace,
            db=runtime.db,
            embedder=runtime.embedder,
            settings=runtime.memory_settings,
        )
        embedding = await try_embed(
            runtime.embedder,
            corrected_statement,
            user_id=self._user_id,
            source="memory tool",
        )

        if target is None:
            draft = NoteDraft(
                kind=kind,
                content=corrected_statement,
                observed_at=datetime.now(UTC),
                subject=subject,
                embedding=embedding,
            )
            await insert_note_with_ops(
                runtime,
                self._user_id,
                draft,
                pipeline="tool",
                op="revise",
                detail="прежняя запись не найдена — сохранена новая",
            )
            return (
                "Прежней записи в памяти не нашёл — сохранил исправленную "
                f"формулировку новой записью: {corrected_statement}"
            )

        # Наследование места знания (kind/subject/журнал/pin/теги) — write_service.
        async with runtime.db.transaction() as tx:
            await revise_with_inheritance(
                runtime,
                self._user_id,
                target,
                corrected_statement,
                embedding=embedding,
                pipeline="tool",
                executor=tx,
            )
        return (
            f"Поправил запись. Было: «{target.content}». Стало: «{corrected_statement}»"
        )

    async def read_log(self, from_turn: int, to_turn: int) -> str:
        """Читает диапазон сырого журнала — страховка от потерь сжатия."""
        if to_turn < from_turn:
            from_turn, to_turn = to_turn, from_turn
        cap = self._runtime.memory_settings.read_log_max_turns
        turns = await self._runtime.turns_repository.turns_range(
            self._user_id,
            from_turn,
            to_turn,
            cap=cap,
        )
        if not turns:
            return f"В логе нет ходов в диапазоне {from_turn}–{to_turn}."
        rendered = "\n\n".join(
            f"— Ход {turn.id} —\n{render_turn_for_reader(turn)}" for turn in turns
        )
        clipped = ""
        if len(turns) == cap and turns[-1].id < to_turn:
            clipped = (
                f"\n\n(показаны первые {cap} ходов диапазона; продолжение — "
                f"запрос с from_turn={turns[-1].id + 1})"
            )
        return f"Сырой лог, ходы {turns[0].id}–{turns[-1].id}:\n\n{rendered}{clipped}"

    async def track(
        self,
        metric: str,
        value: float | None = None,
        unit: str | None = None,
        event_time: datetime | None = None,
        tags: dict[str, str] | None = None,
    ) -> str:
        """Добавляет одно измерение во временной ряд."""
        canonical = normalize_metric_name(metric)
        if not canonical:
            return "Не записал: пустое имя метрики."
        when = _ensure_utc(event_time) or datetime.now(UTC)
        draft = MeasurementDraft(
            metric=canonical,
            event_time=when,
            value=value,
            unit=unit,
            tags=tags or {},
        )
        _, is_new = await self._runtime.measurements_repository.insert(
            self._user_id,
            draft,
        )
        rendered_value = (
            f" = {value}" + (f" {unit}" if unit else "") if value is not None else ""
        )
        suffix = " — новая метрика" if is_new else ""
        return (
            f"Записал: {canonical}{rendered_value} "
            f"({when.strftime('%Y-%m-%d %H:%M')}){suffix}"
        )

    async def stats(
        self,
        metric: str | None = None,
        from_date: datetime | None = None,
        to_date: datetime | None = None,
        group_by: MeasurementBucket | None = None,
    ) -> str:
        """Агрегирует измерения за выбранный период."""
        canonical = normalize_metric_name(metric) if metric else None
        since = _ensure_utc(from_date)
        until = _ensure_utc(to_date)
        # to_date «включительно» в контракте тулзы; полуночная дата означает
        # «весь этот день» — сдвигаем правую границу на сутки.
        if until is not None and until.time() == datetime.min.time():
            until = until + timedelta(days=1)
        aggregates = await self._runtime.measurements_repository.aggregate(
            self._user_id,
            metric=canonical,
            since=since,
            until=until,
            bucket=group_by,
        )
        if not aggregates:
            if canonical:
                return (
                    f"Записей по метрике «{canonical}» за период не найдено. "
                    "memory_stats без аргументов покажет все метрики."
                )
            return "Измерения ещё не велись — записей нет."
        return (
            f"Статистика измерений:\n{render_aggregates(aggregates, bucket=group_by)}"
        )


def _ensure_utc(moment: datetime | None) -> datetime | None:
    """Приводит наивное модельное время к UTC (контракт хранения: только aware-время)."""
    if moment is None:
        return None
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment
