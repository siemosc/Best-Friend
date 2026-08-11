"""Тесты планировщика read-бюджета: clamp-раскладка окна + fail-soft."""

from bestfiend.memory.budget import ReadBudget, plan_read_budget
from bestfiend.memory.settings import MemorySettings


def test_large_window_caps_blocks() -> None:
    """Большое окно: каждый блок упирается в cap, остаток окна свободен."""
    budget = plan_read_budget(128_000, 20_000, 0, MemorySettings())
    assert budget == ReadBudget(
        journal=8_000, profile=3_000, recall=6_000, log_tail=40_000
    )


def test_small_window_holds_floors() -> None:
    """Малое окно: блоки масштабируются долями, выше floor и ниже cap."""
    settings = MemorySettings()
    budget = plan_read_budget(32_000, 4_000, 0, settings)
    assert budget == ReadBudget(
        journal=2_240, profile=840, recall=1_680, log_tail=23_240
    )
    assert budget.journal >= settings.ctx_journal_floor
    assert budget.recall < settings.ctx_recall_cap


def test_large_input_shrinks_memory_without_overflow() -> None:
    """Крупный вход вычитается из working — память ужата, окно не переполнено."""
    settings = MemorySettings()
    window, reserve, input_tokens = 128_000, 20_000, 80_000
    budget = plan_read_budget(window, reserve, input_tokens, settings)
    assert 0 < budget.journal < settings.ctx_journal_cap  # ужата, но жива
    total = budget.journal + budget.profile + budget.recall + budget.log_tail
    assert total + reserve + input_tokens <= window  # инвариант окна


def test_degenerate_window_disables_memory() -> None:
    """working ≤ Σfloor: память выключается, хвост забирает остаток."""
    budget = plan_read_budget(32_000, 20_000, 5_000, MemorySettings())  # working=7000
    assert budget == ReadBudget(journal=0, profile=0, recall=0, log_tail=7_000)


def test_negative_working_zeroes_everything() -> None:
    """Вход+reserve больше окна: всё в 0, хвост не уходит в минус."""
    budget = plan_read_budget(10_000, 20_000, 0, MemorySettings())  # working=-10000
    assert budget == ReadBudget(journal=0, profile=0, recall=0, log_tail=0)
