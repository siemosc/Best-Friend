"""Промпт генерации вопроса-пробы."""

from typing import Final

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from bestfiend.memory.notes.contracts import Note
from bestfiend.memory.recall.render import render_note_line


_PROBE_SYSTEM: Final[str] = """\
Ты — экзаменатор поиска по долгосрочной памяти ассистента. На входе — одна \
запись памяти. Сформулируй вопрос, на который эта запись — прямой ответ: так, \
как его задал бы пользователь, вспоминая это спустя недели. Имена и конкретные \
термины записи в вопросе уместны — по ним и ищут."""


def build_probe_messages(note: Note) -> list[BaseMessage]:
    """Собирает сообщения для генерации вопроса-пробы."""
    return [
        SystemMessage(content=_PROBE_SYSTEM),
        HumanMessage(content=f"Запись памяти:\n{render_note_line(note)}"),
    ]
