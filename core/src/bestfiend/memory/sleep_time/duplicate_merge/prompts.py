"""Промпт проверки пар заметок на дублирование."""

from typing import Final

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from bestfiend.memory.notes.contracts import Note
from bestfiend.memory.recall.render import render_note_line


_MERGE_SYSTEM: Final[str] = """\
Ты — редактор долгосрочной памяти ассистента. На входе — пары записей, похожих \
по формулировке. Для каждой пары реши, говорят ли записи об одном и том же.

- merge=true — записи утверждают одно и то же знание: дай merged_content — \
объединённую формулировку, сохраняющую всю конкретику обеих без повторов.
- merge=false — записи о разном (разные предметы, разные значения, дополняющие \
друг друга факты): обе останутся в памяти как есть.

Каждой паре — ровно одно решение по её pair_index."""


def build_merge_messages(pairs: list[tuple[Note, Note]]) -> list[BaseMessage]:
    """Собирает сообщения для батча решений по парам почти-дублей."""
    blocks = []
    for index, (left, right) in enumerate(pairs):
        blocks.append(
            f"Пара {index} (kind={left.kind}):\n"
            f"  A: {render_note_line(left)}\n"
            f"  B: {render_note_line(right)}"
        )
    return [
        SystemMessage(content=_MERGE_SYSTEM),
        HumanMessage(content="\n\n".join(blocks)),
    ]
