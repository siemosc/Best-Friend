"""Промпт-сборка Observer: system-инструкция + рендер ходов лога."""

from datetime import datetime
from typing import Final

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from bestfiend.memory.entities.contracts import Entity
from bestfiend.memory.notes.contracts import Note
from bestfiend.memory.turns.contracts import Turn
from bestfiend.memory.turns.render import render_turn_for_reader


# Потолки на справочные блоки промпта.
_KNOWN_ENTITIES_MAX: Final[int] = 200
_JOURNAL_TAIL_LINES: Final[int] = 10

_SYSTEM_PROMPT: Final[str] = """\
Ты — летописец долгосрочной памяти ассистента. На входе — свежий фрагмент диалога \
пользователя и ассистента. Извлеки из него записи для памяти.

observations — журнал происходящего:
- Записывай события, решения, выводы и результаты работы: что случилось, что решили, что выяснили, что сделали.
- Каждая запись самодостаточна: конкретные имена, значения и причины прямо в тексте.
- Пиши плотно: одна запись — одно событие, 1-2 предложения. Язык записей — язык диалога.

candidates — знания, которые останутся истинными и полезными через месяцы:
- fact — устойчивый факт о пользователе и его мире.
- preference — как пользователь предпочитает получать ответы и работать.
- rule — правило работы ассистента, выведенное из обратной связи пользователя.

subject — о ком запись:
- user — пользователь и его жизнь: кто он, его окружение, планы, состояние, события с ним.
- agent — сам ассистент: его поведение, его работа, как ему отвечать и действовать.
- world — внешний мир: технологии, проекты, факты и события, не привязанные лично к пользователю.

entities — теги:
- В entities каждой записи перечисляй сущности, к которым она относится: люди, проекты, технологии, организации, повторяющиеся темы.
- Используй имена из списка известных сущностей. Имя, которого в списке нет, добавь в new_entities, если вокруг него в диалоге явно копится контекст.

event_time:
- Заполняй абсолютной датой, когда запись описывает событие с явной временной привязкой в тексте; вычисляй от текущей даты.
- Для вневременных записей оставляй null.

Если фрагмент не содержит ничего достойного памяти — верни пустые списки: \
сырой лог диалога хранится отдельно, потерь нет."""


def build_observer_messages(
    *,
    turns: list[Turn],
    known_entities: list[Entity],
    journal_tail: list[Note],
    now: datetime,
) -> list[BaseMessage]:
    """Собирает [System, Human] для одного прогона Observer."""
    parts = [f"Текущая дата: {now.date().isoformat()}"]
    if known_entities:
        parts.append("Известные сущности:\n" + _render_entities(known_entities))
    if journal_tail:
        parts.append(
            "Хвост журнала (для связности, эти события уже записаны):\n"
            + "\n".join(
                _render_journal_line(note)
                for note in journal_tail[-_JOURNAL_TAIL_LINES:]
            )
        )
    parts.append(
        "Новые ходы диалога:\n"
        + "\n".join(render_turn_for_reader(turn) for turn in turns)
    )
    return [
        SystemMessage(content=_SYSTEM_PROMPT),
        HumanMessage(content="\n\n".join(parts)),
    ]


def _render_entities(entities: list[Entity]) -> str:
    """Список известных сущностей: канон + алиасы."""
    lines: list[str] = []
    for entity in entities[:_KNOWN_ENTITIES_MAX]:
        extra = [
            a for a in entity.aliases if a.lower() != entity.canonical_name.lower()
        ]
        suffix = f" ({', '.join(extra)})" if extra else ""
        lines.append(f"- {entity.canonical_name}{suffix}")
    return "\n".join(lines)


def _render_journal_line(note: Note) -> str:
    """Строка журнала для блока связности."""
    stamp = (note.event_time or note.observed_at).date().isoformat()
    return f"[{stamp}] {note.content}"
