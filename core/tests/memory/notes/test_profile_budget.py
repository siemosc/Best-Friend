"""apply_profile_budget: демоция (use_count asc, observed_at asc) до бюджета секции."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from bestfiend.memory.notes.profile_budget import apply_profile_budget
from bestfiend.memory.settings import MemorySettings
from bestfiend.primitives.tokenizer import count_tokens
from tests.memory.fakes import NoteRepositoryFake, TransactionBuffer, make_note


def _settings(budget: int) -> MemorySettings:
    return MemorySettings(profile_section_token_budget=budget)


@pytest.mark.asyncio
async def test_within_budget_no_demotions() -> None:
    """Секции в бюджете → демоций нет."""
    notes = NoteRepositoryFake(
        pinned=[make_note("краткое", pinned=True, pin_section="preferences")]
    )

    demoted = await apply_profile_budget(
        uuid4(),
        notes_repository=notes,  # type: ignore[arg-type] — стаб по контракту
        settings=_settings(10_000),
        executor=TransactionBuffer(),  # type: ignore[arg-type] - TransactionBuffer как маркер executor
    )

    assert demoted == []


@pytest.mark.asyncio
async def test_overflow_demotes_least_used_then_oldest() -> None:
    """Переполнение → демоция с наименьшим use_count; при равенстве — старейшая."""
    old = datetime(2026, 5, 1, tzinfo=UTC)
    new = datetime(2026, 6, 9, tzinfo=UTC)
    low_use_old = make_note(
        "правило раз", pinned=True, pin_section="rules", use_count=0, observed_at=old
    )
    low_use_new = make_note(
        "правило два", pinned=True, pin_section="rules", use_count=0, observed_at=new
    )
    high_use = make_note(
        "правило три", pinned=True, pin_section="rules", use_count=9, observed_at=old
    )
    notes = NoteRepositoryFake(pinned=[low_use_old, low_use_new, high_use])
    # Бюджет вмещает ровно одну строку — две должны уйти.
    budget = count_tokens(high_use.content)
    tx = TransactionBuffer()

    demoted = await apply_profile_budget(
        uuid4(),
        notes_repository=notes,  # type: ignore[arg-type]
        settings=_settings(budget),
        executor=tx,  # type: ignore[arg-type] - TransactionBuffer как маркер executor
    )

    assert demoted == [low_use_old.id, low_use_new.id]  # use_count, потом возраст
    assert notes.demote_executors == [tx]  # демоция — в транзакции вызывающего
    assert notes.pinned_executors == [tx]  # чтение профиля — тем же executor


@pytest.mark.asyncio
async def test_sections_budgeted_independently() -> None:
    """Переполнена одна секция → другая не тронута."""
    rule_a = make_note("правило раз", pinned=True, pin_section="rules", use_count=0)
    rule_b = make_note("правило два", pinned=True, pin_section="rules", use_count=5)
    identity = make_note("зовут Михаил", pinned=True, pin_section="identity")
    notes = NoteRepositoryFake(pinned=[rule_a, rule_b, identity])
    budget = max(count_tokens(rule_b.content), count_tokens(identity.content))

    demoted = await apply_profile_budget(
        uuid4(),
        notes_repository=notes,  # type: ignore[arg-type]
        settings=_settings(budget),
        executor=TransactionBuffer(),  # type: ignore[arg-type] - TransactionBuffer как маркер executor
    )

    assert demoted == [rule_a.id]
    assert identity.id not in demoted
