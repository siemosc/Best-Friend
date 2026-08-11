"""Пакет AI-интеграции BestFiend.

Слои-фабрики нативных LangChain-объектов из config-dict (таблица models):
`bestfiend.ai.llm` — чат-модели (`build_chat_model`), `bestfiend.ai.embeddings` —
эмбеддеры (`build_embeddings`). Общий passthrough-конфиг — `bestfiend.ai.config`.
Rerank пока вне core.
"""
