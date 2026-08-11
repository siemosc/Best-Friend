"""Промпт-сборка Reconciler: инструкция + рендер кандидатов с соседями."""

from typing import Final

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from bestfiend.memory.notes.contracts import Note, NoteDraft


_SYSTEM_PROMPT: Final[str] = """\
Ты — хранитель связности долгосрочной памяти ассистента. На входе — знания-кандидаты, \
извлечённые из свежего фрагмента диалога, и для каждого — соседние записи, уже \
хранящиеся в памяти. Прими решение по каждому кандидату.

action:
- add — кандидат несёт новое знание: соседей нет, они о другом или дополняют его.
- noop — знание уже записано: один из соседей утверждает то же самое.
- supersede — кандидат обновляет соседа: тот же предмет, новое актуальное значение \
(перемена в жизни пользователя, уточнение, новая версия предпочтения). \
В target_index укажи обновляемого соседа.
- contradict — кандидат и сосед несовместимы, и по тексту не определить, что вернее. \
В target_index укажи конфликтующего соседа. Обе записи останутся в памяти с пометкой \
конфликта — ассистент уточнит у пользователя в диалоге.

pin — место хранения:
- pin=true переносит знание в постоянный профиль, который ассистент видит в каждом \
ответе. Туда идут устойчивые предпочтения, правила работы и факты о самом \
пользователе и его постоянном окружении. Укажи pin_section.
- pin=false оставляет знание в архиве: оно находится поиском по запросу. Эпизоды, \
рабочий контекст и частности живут в архиве.

Для кандидата без соседей действия — add или noop (supersede и contradict требуют \
соседа); главный вопрос по нему — pin.

В decisions — ровно одно решение на каждого кандидата по его candidate_index."""


def build_reconciler_messages(
    candidates: list[NoteDraft],
    neighbors_by_candidate: list[list[Note]],
) -> list[BaseMessage]:
    """Собирает [System, Human] для одного батч-вызова Reconciler."""
    blocks: list[str] = []
    for index, (candidate, neighbors) in enumerate(
        zip(candidates, neighbors_by_candidate, strict=True)
    ):
        lines = [f"Кандидат {index} (kind={candidate.kind}): «{candidate.content}»"]
        if neighbors:
            lines.append(f"Соседи кандидата {index}:")
            lines.extend(
                f"  {n_index}. {_render_neighbor(note)}"
                for n_index, note in enumerate(neighbors)
            )
        else:
            lines.append(f"Соседи кандидата {index}: нет")
        blocks.append("\n".join(lines))
    return [
        SystemMessage(content=_SYSTEM_PROMPT),
        HumanMessage(content="\n\n".join(blocks)),
    ]


def _render_neighbor(note: Note) -> str:
    """Строка соседа: дата, kind, статус-маркеры, контент."""
    stamp = (note.event_time or note.observed_at).date().isoformat()
    markers = [note.kind]
    if note.status == "contradicted":
        markers.append("в конфликте")
    if note.pinned:
        markers.append("в профиле")
    return f"[{stamp}] ({', '.join(markers)}) «{note.content}»"
