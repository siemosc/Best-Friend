"""MemoryRuntime.start: fail-soft по конфигам моделей — старт core не блокируется."""

from typing import Any
from unittest.mock import AsyncMock

import pytest

from bestfiend.memory.runtime import MemoryRuntime
from bestfiend.memory.settings import MemorySettings


def _runtime(loader: Any) -> MemoryRuntime:
    return MemoryRuntime(
        db=AsyncMock(),
        turns_repository=AsyncMock(),
        notes_repository=AsyncMock(),
        entities_repository=AsyncMock(),
        watermarks_repository=AsyncMock(),
        ops_repository=AsyncMock(),
        probes_repository=AsyncMock(),
        measurements_repository=AsyncMock(),
        memory_settings=MemorySettings(),
        model_config_loader=loader,
    )


@pytest.mark.asyncio
async def test_start_survives_malformed_embedding_config() -> None:
    """Кривой конфиг эмбеддера (фабрика бросает) → start ok, embedder None, observer жив."""

    async def loader(model_id: str) -> dict[str, Any]:
        if "embedding" in model_id:
            return {"provider": "openrouter"}  # нет 'model' → AIConfig бросит
        return {"provider": "openrouter", "model": "llm-stub"}

    runtime = _runtime(loader)

    await runtime.start()  # не бросает

    assert runtime.embedder is None
    assert runtime.observer is not None
    assert runtime.observer.is_enabled is True


@pytest.mark.asyncio
async def test_start_survives_loader_failure() -> None:
    """Сбой загрузчика конфигов → start ok, оба слота выключены."""

    async def loader(model_id: str) -> dict[str, Any]:
        raise RuntimeError("db down")

    runtime = _runtime(loader)

    await runtime.start()

    assert runtime.embedder is None
    assert runtime.observer is not None
    assert runtime.observer.is_enabled is False


@pytest.mark.asyncio
async def test_start_missing_model_id_disables_slot() -> None:
    """Отсутствующий id в models (loader → None) → слот выключен, старт ok."""

    async def loader(model_id: str) -> None:
        return None

    runtime = _runtime(loader)

    await runtime.start()

    assert runtime.embedder is None
    assert runtime.observer is not None
    assert runtime.observer.is_enabled is False
