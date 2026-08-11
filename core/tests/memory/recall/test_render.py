"""Рендеры блоков памяти: журнал, профиль, recall; пустые входы → пустые строки."""

from datetime import UTC, datetime
from uuid import uuid4

from bestfiend.memory.notes.contracts import Note
from bestfiend.memory.recall.render import (
    render_journal,
    render_note_line,
    render_profile,
    render_recall,
)


def _note(
    content: str,
    *,
    kind: str = "observation",
    event_time: datetime | None = None,
    pin_section: str | None = None,
    status: str = "active",
) -> Note:
    return Note(
        id=uuid4(),
        user_id=uuid4(),
        kind=kind,
        subject=None,
        content=content,
        event_time=event_time,
        observed_at=datetime(2026, 6, 9, 15, 0, tzinfo=UTC),
        status=status,
        pinned=pin_section is not None,
        pin_section=pin_section,
        in_journal=kind == "observation",
        journal_weight=1,
        source_turn_start=None,
        source_turn_end=None,
        use_count=0,
    )


def test_note_line_prefers_event_time() -> None:
    """Дата строки — event_time события; без него — дата записи."""
    dated = _note("переезд", event_time=datetime(2026, 3, 1, tzinfo=UTC))
    undated = _note("факт")

    assert render_note_line(dated) == "[2026-03-01] переезд"
    assert render_note_line(undated) == "[2026-06-09] факт"


def test_journal_renders_dated_lines() -> None:
    """Журнал: заголовок + датированные строки в порядке списка."""
    text = render_journal([_note("первое"), _note("второе")])

    assert text.startswith("## Журнал наблюдений")
    assert "[2026-06-09] первое" in text
    assert text.index("первое") < text.index("второе")


def test_profile_groups_by_section() -> None:
    """Профиль: секции в фиксированном порядке, заметки маркированным списком."""
    text = render_profile(
        [
            _note("любит краткость", kind="preference", pin_section="preferences"),
            _note("зовут Артём", kind="fact", pin_section="identity"),
        ]
    )

    assert text.startswith("## Профиль пользователя")
    assert "### Кто пользователь" in text
    assert "- зовут Артём" in text
    assert text.index("Кто пользователь") < text.index("Предпочтения")


def test_empty_inputs_render_empty() -> None:
    """Пустые списки → пустые строки (блок не попадает в промпт)."""
    assert render_journal([]) == ""
    assert render_profile([]) == ""
    assert render_recall([]) == ""


def test_recall_block_header_and_lines() -> None:
    """Recall-блок: заголовок + датированные строки."""
    text = render_recall(
        [_note("нашлось", event_time=datetime(2026, 1, 5, tzinfo=UTC))]
    )

    assert text.startswith("## Из памяти")
    assert "[2026-01-05] нашлось" in text


def test_contradicted_note_line_carries_conflict_marker() -> None:
    """Contradicted-заметка рендерится с ⚠️ и пометкой конфликта."""
    line = render_note_line(_note("пьёт кофе", kind="fact", status="contradicted"))

    assert line.startswith("[2026-06-09] ⚠️ пьёт кофе")
    assert "противоречит" in line
