"""Автопробы: вопрос → боевой recall → hit/rank; сбои скипают пробу, не цикл."""

from typing import Any
from uuid import UUID, uuid4

import pytest

from bestfiend.memory.notes.contracts import Note
from bestfiend.memory.sleep_time.probes import run_probes
from bestfiend.memory.sleep_time.probes import service as probes
from bestfiend.memory.sleep_time.probes.schemas import ProbeOutput
from tests.memory.fakes import NoteRepositoryFake, make_note

from .conftest import make_ctx


class ProbesRepoStub:
    """Копит записанные пробы."""

    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    async def record(
        self,
        user_id: UUID,
        *,
        question: str,
        expected_note_id: UUID,
        hit: bool,
        rank: int | None,
    ) -> None:
        self.records.append(
            {
                "question": question,
                "expected_note_id": expected_note_id,
                "hit": hit,
                "rank": rank,
            }
        )


def stub_observer_llm(
    monkeypatch: pytest.MonkeyPatch, output: ProbeOutput | None
) -> None:
    async def fake_invoke(ctx: Any, schema: Any, messages: Any, **kwargs: Any) -> Any:
        return output

    monkeypatch.setattr(probes, "invoke_structured", fake_invoke)


def _stub_recall(monkeypatch: pytest.MonkeyPatch, found: list[Note]) -> None:
    async def fake_recall(**kwargs: Any) -> list[Note]:
        return found

    monkeypatch.setattr(probes, "recall_notes", fake_recall)


@pytest.mark.asyncio
async def test_probe_records_hit_with_rank(monkeypatch: pytest.MonkeyPatch) -> None:
    """Заметка нашлась второй → hit=true, rank=2."""
    target = make_note("решение о хранилище")
    other = make_note("другая заметка")
    notes = NoteRepositoryFake()
    notes.recent_sample = [target]
    repo = ProbesRepoStub()
    stub_observer_llm(monkeypatch, ProbeOutput(question="что решили про хранилище?"))
    _stub_recall(monkeypatch, [other, target])

    await run_probes(uuid4(), make_ctx(notes=notes), repo)  # type: ignore[arg-type]

    [record] = repo.records
    assert record["expected_note_id"] == target.id
    assert record["hit"] is True
    assert record["rank"] == 2


@pytest.mark.asyncio
async def test_probe_records_miss(monkeypatch: pytest.MonkeyPatch) -> None:
    """Заметка не в выдаче → hit=false, rank=None."""
    target = make_note("потерянное знание")
    notes = NoteRepositoryFake()
    notes.recent_sample = [target]
    repo = ProbesRepoStub()
    stub_observer_llm(monkeypatch, ProbeOutput(question="вопрос"))
    _stub_recall(monkeypatch, [])

    await run_probes(uuid4(), make_ctx(notes=notes), repo)  # type: ignore[arg-type]

    [record] = repo.records
    assert record["hit"] is False
    assert record["rank"] is None


@pytest.mark.asyncio
async def test_probe_llm_failure_skips_probe_not_cycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Сбой LLM первой пробы → вторая записана."""
    first, second = make_note("раз"), make_note("два")
    notes = NoteRepositoryFake()
    notes.recent_sample = [first, second]
    repo = ProbesRepoStub()
    outputs: list[ProbeOutput | None] = [None, ProbeOutput(question="вопрос про два")]

    async def flaky_invoke(ctx: Any, schema: Any, messages: Any, **kwargs: Any) -> Any:
        return outputs.pop(0)

    monkeypatch.setattr(probes, "invoke_structured", flaky_invoke)
    _stub_recall(monkeypatch, [second])

    await run_probes(uuid4(), make_ctx(notes=notes), repo)  # type: ignore[arg-type]

    [record] = repo.records
    assert record["expected_note_id"] == second.id
    assert record["rank"] == 1
