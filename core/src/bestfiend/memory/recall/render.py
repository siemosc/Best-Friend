"""Рендер блоков памяти в текст промпта: журнал, профиль, recall-блок."""

from typing import Final

from bestfiend.memory.notes.contracts import Note


_PIN_SECTION_TITLES: Final[dict[str, str]] = {
    "identity": "Кто пользователь",
    "preferences": "Предпочтения",
    "relationships": "Окружение",
    "rules": "Правила работы",
}
_PIN_SECTION_ORDER: Final[tuple[str, ...]] = (
    "identity",
    "preferences",
    "relationships",
    "rules",
)
_NO_SECTION_TITLE: Final[str] = "Прочее"


def render_note_line(note: Note) -> str:
    """Одна строка заметки: дата + контент (дата события, иначе дата записи).

    Contradicted-заметка несёт маркер конфликта — агент видит обе стороны
    в recall и уточняет у пользователя естественно, в диалоге.
    """
    stamp = (note.event_time or note.observed_at).date().isoformat()
    if note.status == "contradicted":
        return (
            f"[{stamp}] ⚠️ {note.content} "
            "(противоречит другой записи в памяти — стоит уточнить)"
        )
    return f"[{stamp}] {note.content}"


def render_note_line_with_span(note: Note) -> str:
    """Строка заметки + span ходов-источников (для выдачи memory_search).

    Span — мост к memory_read_log: агент может дойти от заметки до дословной
    сцены в сыром логе. В авто-recall span не рендерится (мусор в контексте).
    """
    line = render_note_line(note)
    if note.source_turn_start is not None and note.source_turn_end is not None:
        return f"{line} (ходы {note.source_turn_start}–{note.source_turn_end})"
    return line


def render_journal(notes: list[Note]) -> str:
    """Журнал наблюдений для system-блока; пустой журнал → пустая строка."""
    if not notes:
        return ""
    lines = "\n".join(render_note_line(note) for note in notes)
    return f"## Журнал наблюдений\n\n{lines}"


def render_profile(notes: list[Note]) -> str:
    """Профиль (pinned-заметки) по секциям; пустой профиль → пустая строка."""
    if not notes:
        return ""
    by_section: dict[str, list[Note]] = {}
    for note in notes:
        by_section.setdefault(note.pin_section or "", []).append(note)

    blocks: list[str] = []
    ordered = [*_PIN_SECTION_ORDER, ""]
    for section in ordered:
        section_notes = by_section.get(section)
        if not section_notes:
            continue
        title = _PIN_SECTION_TITLES.get(section, _NO_SECTION_TITLE)
        lines = "\n".join(f"- {note.content}" for note in section_notes)
        blocks.append(f"### {title}\n{lines}")
    return "## Профиль пользователя\n\n" + "\n\n".join(blocks)


def render_recall(notes: list[Note]) -> str:
    """Recall-блок (найденное в архиве); пустой список → пустая строка (gate)."""
    if not notes:
        return ""
    lines = "\n".join(render_note_line(note) for note in notes)
    return f"## Из памяти (найдено по текущему запросу)\n\n{lines}"
