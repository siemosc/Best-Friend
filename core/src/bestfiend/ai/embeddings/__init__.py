"""Embeddings-слой: фабрика нативных Embeddings (LangChain) из config-dict."""

from bestfiend.ai.embeddings.factory import build_embeddings


__all__ = [
    "build_embeddings",
]
