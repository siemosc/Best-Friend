"""ai-embeddings factory: построение нативного `Embeddings` из config-dict модели.

Симметрия с ai/llm/factory: config-dict (provider/model/ключи/параметры) из таблицы
models → langchain `Embeddings`. `openrouter`/`openai`/`llamacpp` говорят по
OpenAI-совместимому протоколу → `OpenAIEmbeddings`; `ollama` → нативный
`OllamaEmbeddings`. Здесь только создание модели, без вызовов.
"""

from typing import Any

from langchain_core.embeddings import Embeddings
from langchain_ollama import OllamaEmbeddings
from langchain_openai import OpenAIEmbeddings

from bestfiend.ai.config import AIConfig


# Эндпоинт embeddings у OpenRouter зашит, как и у chat — api_base из конфига не нужен.
_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# Дефолт таймаута запроса эмбеддинга, если в конфиге нет timeout_s — чтобы мёртвый
# эндпоинт падал в ошибку, а не висел бесконечно.
_DEFAULT_TIMEOUT_S = 120.0


def build_embeddings(config: dict[str, Any]) -> Embeddings:
    """Создаёт `Embeddings` из config-dict (provider/model/ключи/параметры)."""
    cfg = AIConfig(config)
    if cfg.provider == "ollama":
        return _build_ollama_embeddings(cfg)
    return _build_openai_compatible_embeddings(cfg)


def _build_openai_compatible_embeddings(cfg: AIConfig) -> OpenAIEmbeddings:
    """openrouter/openai/llamacpp → `OpenAIEmbeddings` (OpenAI-совместимый эндпоинт)."""
    kwargs = cfg.as_kwargs()
    # encoding_format — не поле класса, штатно едет через model_kwargs; 'float'
    # избавляет от base64-декода ответа на не-OpenAI провайдерах.
    model_kwargs = dict(kwargs.pop("model_kwargs", None) or {})
    model_kwargs.setdefault("encoding_format", "float")
    kwargs["model_kwargs"] = model_kwargs
    # Не-OpenAI эндпоинты не понимают tiktoken-токены — шлём сырой текст. setdefault:
    # настоящий OpenAI в конфиге может вернуть True ради токенного чанкинга.
    kwargs.setdefault("check_embedding_ctx_length", False)
    if cfg.provider == "openrouter":
        kwargs["base_url"] = _OPENROUTER_BASE_URL
    elif cfg.api_base is not None:
        kwargs["base_url"] = cfg.api_base
    if cfg.api_key is not None:
        kwargs["api_key"] = cfg.api_key
    # request_timeout (alias timeout) — в секундах, в отличие от ChatOpenRouter (мс).
    kwargs["timeout"] = (
        cfg.timeout_s if cfg.timeout_s is not None else _DEFAULT_TIMEOUT_S
    )
    kwargs.setdefault("max_retries", 0)  # ретраями владеет вызывающий слой, не клиент
    return OpenAIEmbeddings(model=cfg.model, **kwargs)


def _build_ollama_embeddings(cfg: AIConfig) -> OllamaEmbeddings:
    """ollama → нативный `OllamaEmbeddings` (/api/embed, батч одним запросом).

    `extra='forbid'` у класса → passthrough фильтруем по `model_fields`, иначе лишнее
    поле из конфига валит конструктор. timeout/api_key не шлём: сервер локальный,
    рабочий таймаут требует client_kwargs (отложено).
    """
    valid_fields = set(OllamaEmbeddings.model_fields)
    kwargs = {k: v for k, v in cfg.as_kwargs().items() if k in valid_fields}
    if cfg.api_base is not None:
        kwargs["base_url"] = cfg.api_base  # без /v1 — нативный эндпоинт ollama
    return OllamaEmbeddings(model=cfg.model, **kwargs)
