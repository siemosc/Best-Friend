"""Промпт генерации карточки сущности."""

from typing import Final

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from bestfiend.memory.notes.contracts import Note
from bestfiend.memory.recall.render import render_note_line


_CARD_SYSTEM: Final[str] = """\
Ты — куратор досье в долгосрочной памяти ассистента. На входе — заметки памяти \
об одной сущности (человеке, проекте, теме) и, если есть, прежняя версия карточки. \
Собери одну актуальную карточку: «всё важное про X» одним связным документом.

- Открой карточку строкой, называющей сущность и её роль в мире пользователя.
- Дальше — текущее состояние, ключевые факты, решения и события с датами; \
конкретные имена и значения прямо в тексте.
- Свежие заметки уточняют и вытесняют устаревшие утверждения прежней карточки.
- Пиши плотно: карточка выигрывает у россыпи заметок за счёт плотности."""


def build_card_messages(
    entity_name: str,
    notes: list[Note],
    previous_card: Note | None,
) -> list[BaseMessage]:
    """Собирает сообщения для генерации карточки сущности."""
    parts = [f"Сущность: {entity_name}"]
    if previous_card is not None:
        parts.append(f"Прежняя карточка:\n{previous_card.content}")
    parts.append(
        "Заметки памяти (свежие сверху):\n"
        + "\n".join(f"- {render_note_line(note)}" for note in notes)
    )
    return [
        SystemMessage(content=_CARD_SYSTEM),
        HumanMessage(content="\n\n".join(parts)),
    ]
