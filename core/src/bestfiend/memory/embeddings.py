"""Эмбеддер памяти: обёртка над ai/embeddings с MRL-усечением до фиксированной размерности.

Qwen3-Embedding обучен с Matryoshka Representation Learning — официальный способ
получить меньшую размерность: усечь вектор и перенормировать. Усечение делаем на
своей стороне → независимость от поддержки параметра dimensions у провайдера.
Размерность фиксирована схемой БД (vector(1024)) — смена модели эмбеддингов
требует пересчёта архива, не подбора размерности.
"""

import math
from typing import Any
from uuid import UUID

from langchain_core.embeddings import Embeddings
from langfuse import get_client
from loguru import logger

from bestfiend.ai.embeddings import build_embeddings


class MemoryEmbedder:
    """Векторизация текстов заметок/запросов с приведением к размерности БД.

    Каждый вызов оборачивается Langfuse-спаном типа embedding: langchain
    Embeddings не эмитят callbacks, без ручного спана вызовы эмбеддера
    невидимы в трейсе.
    """

    __slots__ = ("_dim", "_embeddings", "_model_name")

    def __init__(self, embeddings: Embeddings, dim: int, model_name: str = "") -> None:
        self._embeddings = embeddings
        self._dim = dim
        self._model_name = model_name

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Векторизует батч текстов (запись заметок)."""
        with get_client().start_as_current_observation(
            name="memory.embed_documents",
            as_type="embedding",
            input={"texts": texts},
            model=self._model_name or None,
        ) as span:
            raw = await self._embeddings.aembed_documents(texts)
            vectors = [_shrink(vector, self._dim) for vector in raw]
            span.update(output={"vectors": len(vectors), "dim": self._dim})
            return vectors

    async def embed_query(self, text: str) -> list[float]:
        """Векторизует поисковый запрос (recall)."""
        with get_client().start_as_current_observation(
            name="memory.embed_query",
            as_type="embedding",
            input={"text": text},
            model=self._model_name or None,
        ) as span:
            vector = _shrink(await self._embeddings.aembed_query(text), self._dim)
            span.update(output={"dim": self._dim})
            return vector


def build_memory_embedder(config: dict[str, Any], dim: int) -> MemoryEmbedder:
    """Создаёт эмбеддер памяти из config-dict модели (таблица models)."""
    return MemoryEmbedder(
        build_embeddings(config), dim, model_name=str(config.get("model") or "")
    )


async def try_embed_documents(
    embedder: MemoryEmbedder | None,
    texts: list[str],
    *,
    user_id: UUID,
    source: str,
) -> list[list[float] | None]:
    """Векторы батча текстов; нет эмбеддера или сбой → None'ы (запись важнее вектора).

    Один fail-soft паттерн на всех писателей заметок; записи без вектора
    находит FTS-ветка recall. `source` — префикс warning-лога.
    """
    if embedder is None or not texts:
        return [None] * len(texts)
    try:
        vectors = await embedder.embed_documents(texts)
    except Exception as exc:  # noqa: BLE001 — запись важнее вектора
        logger.warning("{}: embedding failed user_id={}: {}", source, user_id, exc)
        return [None] * len(texts)
    return list(vectors)


async def try_embed(
    embedder: MemoryEmbedder | None,
    text: str,
    *,
    user_id: UUID,
    source: str,
) -> list[float] | None:
    """Вектор одного текста; нет эмбеддера или сбой → None."""
    [vector] = await try_embed_documents(
        embedder, [text], user_id=user_id, source=source
    )
    return vector


def _shrink(vector: list[float], dim: int) -> list[float]:
    """MRL-усечение до dim + L2-normalize (косинус по усечённому корректен после нормировки)."""
    head = vector[:dim]
    norm = math.sqrt(sum(x * x for x in head))
    if norm == 0.0:
        return head
    return [x / norm for x in head]
