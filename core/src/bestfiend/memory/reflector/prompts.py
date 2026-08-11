"""Промпт-сборка Reflector: инструкция + рендер строк журнала с индексами."""

from typing import Final

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from bestfiend.memory.notes.contracts import Note


_WEIGHT_LABELS: Final[dict[int, str]] = {2: "high", 1: "mid", 0: "low"}

_SYSTEM_PROMPT: Final[str] = """\
Ты — редактор журнала наблюдений ассистента. Журнал вырос за пределы бюджета \
контекста — сожми его примерно вдвое, сохранив рабочую ценность.

reflections — сводные записи:
- Сворачивай группы строк об одной теме или линии событий в одну плотную запись: \
итог, конкретные имена, значения и ключевые даты прямо в тексте.
- В source_indexes перечисли индексы всех свёрнутых строк.
- weight: high — решения и факты о пользователе; mid — рабочий контекст; low — фон.

evict_indexes — строки, чья ценность исчерпана: разовые детали и шаги, \
превзойдённые ходом событий. Они уходят из журнала без свёртки (полный лог \
диалога хранится отдельно, потерь нет).

Строки, не попавшие ни в source_indexes, ни в evict_indexes, остаются в журнале \
как есть. Свежие строки обычно ценнее старых; high-строки сохраняют конкретику \
в первую очередь."""


def build_reflector_messages(journal: list[Note]) -> list[BaseMessage]:
    """Собирает [System, Human] для одного прогона Reflector."""
    lines = [
        f"{index}. {_render_journal_line(note)}" for index, note in enumerate(journal)
    ]
    return [
        SystemMessage(content=_SYSTEM_PROMPT),
        HumanMessage(content="Строки журнала:\n" + "\n".join(lines)),
    ]


def _render_journal_line(note: Note) -> str:
    """Строка журнала: дата + weight + контент."""
    stamp = (note.event_time or note.observed_at).date().isoformat()
    weight = _WEIGHT_LABELS.get(note.journal_weight, "mid")
    return f"[{stamp}] ({weight}) {note.content}"
