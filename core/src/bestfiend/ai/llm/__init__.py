"""LLM-слой: фабрика нативных чат-моделей LangChain из config-dict.

Общий passthrough-конфиг (`AIConfig`) живёт в `bestfiend.ai.config`.
"""

from bestfiend.ai.llm.factory import build_chat_model


__all__ = [
    "build_chat_model",
]
