"""Langfuse-обвязка памяти: callbacks для LLM-вызовов фоновых пайплайнов.

Спаны вокруг поиска/записи создаются по месту через `langfuse.get_client()`
(паттерн graph/runtime): при выключенном трейсинге клиент отключён и спаны
no-op. Здесь — общий конфиг langchain-вызовов: с ним structured-вызовы
Observer/Reconciler/Reflector/sleep видны в трейсе generation'ами с полным
промптом и ответом, припаренченными к текущему спану пайплайна.
"""

from langchain_core.runnables import RunnableConfig
from langfuse.langchain import CallbackHandler


def llm_run_config() -> RunnableConfig:
    """RunnableConfig с Langfuse-handler'ом: generation парентится к текущему спану."""
    return {"callbacks": [CallbackHandler()]}
