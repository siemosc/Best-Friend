"""Планировщик read-бюджета: доли окна модели графа под блоки памяти.

Чистая функция без I/O. Вход и запас на ответ вычитаются из окна первыми (их
не ужать), остаток раскладывается плоско — каждый блок берёт долю, зажатую
floor/cap. Подробности — в docs/memory_architecture_alt.md, раздел «Бюджет
контекста».
"""

from dataclasses import dataclass

from loguru import logger

from bestfiend.memory.settings import MemorySettings


@dataclass(frozen=True, slots=True)
class ReadBudget:
    """Токен-бюджеты блоков памяти под одно окно модели графа.

    journal/profile — резерв места (на read не режутся, идут в расчёт остатка),
    recall/log_tail — жёсткие бюджеты соответствующих веток поиска.
    """

    journal: int
    profile: int
    recall: int
    log_tail: int


def plan_read_budget(
    window: int,
    output_reserve: int,
    input_tokens: int,
    settings: MemorySettings,
) -> ReadBudget:
    """Раскладывает окно модели графа по блокам памяти под текущий запрос.

    При нехватке остатка даже на минимумы — память выключается (блоки в 0),
    хвост забирает остаток: свежие ходы приоритетнее памяти.
    """
    working = window - output_reserve - input_tokens
    floors_total = (
        settings.ctx_journal_floor
        + settings.ctx_profile_floor
        + settings.ctx_recall_floor
        + settings.ctx_log_tail_floor
    )
    if working <= floors_total:
        logger.warning(
            "plan_read_budget: working={} ≤ Σfloor={} — память off, хвост ужат",
            working,
            floors_total,
        )
        return ReadBudget(journal=0, profile=0, recall=0, log_tail=max(working, 0))

    journal = _clamp(
        int(working * settings.ctx_journal_pct),
        settings.ctx_journal_floor,
        settings.ctx_journal_cap,
    )
    profile = _clamp(
        int(working * settings.ctx_profile_pct),
        settings.ctx_profile_floor,
        settings.ctx_profile_cap,
    )
    recall = _clamp(
        int(working * settings.ctx_recall_pct),
        settings.ctx_recall_floor,
        settings.ctx_recall_cap,
    )
    log_tail = _clamp(
        working - journal - profile - recall,
        settings.ctx_log_tail_floor,
        settings.ctx_log_tail_cap,
    )
    return ReadBudget(
        journal=journal, profile=profile, recall=recall, log_tail=log_tail
    )


def _clamp(value: int, floor: int, cap: int) -> int:
    """Зажимает значение в [floor … cap]."""
    return max(floor, min(value, cap))
