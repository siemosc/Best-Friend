"""Промпт генерации недельной сводки."""

from typing import Final

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from bestfiend.memory.notes.contracts import Note
from bestfiend.memory.recall.render import render_note_line


_SUMMARY_SYSTEM: Final[str] = """\
Ты — летописец долгосрочной памяти ассистента. На входе — наблюдения журнала \
за одну неделю и, если пользователь вёл учёт, агрегаты его измерений за ту же \
неделю (вес, сон, тренировки и другие ряды). Сведи всё в одну плотную сводку недели.

- Главные события, решения и результаты — с датами и конкретикой.
- Линию событий своди в итог: что началось, чем закончилось, что решили.
- Цифры измерений вплетай в сводку там, где они добавляют факта: значения, \
динамика, количество раз.
- Открой сводку строкой с границами недели. Пиши плотно, без воды."""


def build_summary_messages(
    week_start_iso: str,
    week_end_iso: str,
    notes: list[Note],
    *,
    measurements_digest: str | None = None,
) -> list[BaseMessage]:
    """Собирает сообщения для недельной сводки."""
    parts = [f"Неделя {week_start_iso} — {week_end_iso}."]
    if notes:
        lines = "\n".join(f"- {render_note_line(note)}" for note in notes)
        parts.append(f"Наблюдения недели:\n{lines}")
    if measurements_digest:
        parts.append(f"Измерения недели (агрегаты):\n{measurements_digest}")
    return [
        SystemMessage(content=_SUMMARY_SYSTEM),
        HumanMessage(content="\n".join(parts)),
    ]
