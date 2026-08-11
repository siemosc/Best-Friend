"""ai-factory: построение нативного `BaseChatModel` из config-dict модели.

config-dict приходит из таблицы models (per-user resolve в model_registry для
графа; прямой lookup для памяти) — гибкий формат provider/model/ключи/параметры
генерации. Factory маппит его на langchain: `openrouter` → `ChatOpenRouter`
(нативный `openrouter_provider`), остальные провайдеры → `init_chat_model`.
Здесь только создание модели, без вызовов.
"""

from typing import Any

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel
from langchain_openrouter import ChatOpenRouter
from loguru import logger

from bestfiend.ai.config import AIConfig
from bestfiend.ai.llm.ollama import ChatOllamaWithExtraSampling


# llamacpp говорит по OpenAI-совместимому протоколу → langchain-провайдер "openai".
_PROVIDER_TO_LANGCHAIN: dict[str, str] = {"llamacpp": "openai"}

# Поля ChatOpenRouter (снимок при импорте — в тестах класс подменяется спаем):
# ключи extra_body с одноимённым нативным полем мапятся в конструктор.
_OPENROUTER_NATIVE_FIELDS = frozenset(ChatOpenRouter.model_fields)

# Легаси-формат выключения reasoning из конфигов старого OpenAI-стека (extra_body
# уходил в payload как есть). Схема openrouter-SDK знает только effort/summary —
# "enabled" молча отрезался бы сериализатором, thinking остался бы включён.
_LEGACY_REASONING_DISABLED: dict[str, Any] = {"enabled": False}

# Уходит в bind_tools на этапе графа, не в конструктор модели.
_BIND_TIME_FIELDS = frozenset({"parallel_tool_calls"})

# Дефолт таймаута LLM-вызова, если в конфиге нет timeout_s — чтобы мёртвый/висящий
# эндпоинт падал в ошибку (→ error-нода → static-ответ), а не висел бесконечно.
_DEFAULT_TIMEOUT_S = 120.0


def build_chat_model(config: dict[str, Any]) -> BaseChatModel:
    """Создаёт `BaseChatModel` из config-dict (provider/model/ключи/параметры генерации)."""
    cfg = AIConfig(config)
    if cfg.provider == "openrouter":
        return _build_openrouter(cfg)
    if cfg.provider == "ollama":
        return _build_ollama(cfg)
    return _build_via_init(cfg)


def _passthrough_kwargs(cfg: AIConfig) -> dict[str, Any]:
    """Параметры генерации без client- и bind-time-полей (temperature/max_tokens/...)."""
    return {k: v for k, v in cfg.as_kwargs().items() if k not in _BIND_TIME_FIELDS}


def _build_openrouter(cfg: AIConfig) -> ChatOpenRouter:
    """openrouter → `ChatOpenRouter`; ключи `extra_body` → нативные поля модели.

    Произвольный extra_body нативный стек не умеет: ChatOpenRouter ссыпал бы его
    в model_kwargs → `Chat.send_async(**params)`, а у SDK строгая сигнатура —
    нераспознанный kwarg = TypeError на каждом вызове. Поэтому known-ключи
    (reasoning/plugins/...) мапим в одноимённые поля, provider — в
    openrouter_provider, остаток дропаем с warning.
    """
    kwargs = _passthrough_kwargs(cfg)
    extra_body = kwargs.pop("extra_body", None)
    if isinstance(extra_body, dict):
        # Копия: as_kwargs отдаёт ту же вложенную ссылку, что в config от
        # model_registry — pop из оригинала исказил бы переиспользуемый конфиг.
        remaining = dict(extra_body)
        provider_routing = remaining.pop("provider", None)
        if provider_routing is not None:
            kwargs["openrouter_provider"] = provider_routing
        for key in [k for k in remaining if k in _OPENROUTER_NATIVE_FIELDS]:
            kwargs[key] = remaining.pop(key)
        if remaining:
            logger.warning(
                "ai-factory: extra_body не поддержан нативным OpenRouter-стеком, "
                "ключи опущены: {}",
                sorted(remaining),
            )
    if kwargs.get("reasoning") == _LEGACY_REASONING_DISABLED:
        # Нативный эквивалент выключения по схеме SDK.
        kwargs["reasoning"] = {"effort": "none"}
    if cfg.api_key is not None:
        kwargs["api_key"] = cfg.api_key  # alias → openrouter_api_key
    # ChatOpenRouter.timeout — в МИЛЛИСЕКУНДАХ (alias → request_timeout → SDK timeout_ms),
    # в отличие от ChatOpenAI (секунды). Конвертируем, иначе таймаут абсурдно мал.
    timeout_s = cfg.timeout_s if cfg.timeout_s is not None else _DEFAULT_TIMEOUT_S
    kwargs["timeout"] = int(timeout_s * 1000)
    # Ретраями владеет langgraph (RETRY_POLICY на react), не клиент — иначе двойной слой.
    kwargs.setdefault("max_retries", 0)
    # app_url/app_title по дефолту непусты ("docs.langchain.com"/"LangChain") →
    # ChatOpenRouter инжектит собственный httpx.AsyncClient ради attribution-заголовков,
    # а на пользовательском клиенте SDK НЕ применяет timeout_ms → запрос виснет вечно.
    # Гасим заголовки (None) → SDK берёт свой клиент, таймаут работает. Attribution в
    # дашборде OpenRouter некритична.
    kwargs["app_url"] = None
    kwargs["app_title"] = None
    # base_url у openrouter зашит в классе — api_base не пробрасываем.
    return ChatOpenRouter(model=cfg.model, **kwargs)


def _build_ollama(cfg: AIConfig) -> ChatOllamaWithExtraSampling:
    """ollama → нативный ChatOllama (/api/chat, раздельный thinking) + sampling-опции.

    Таймаут и api_key не пробрасываем: сервер локальный, ключ не нужен, а рабочий
    таймаут ChatOllama требует client_kwargs (отложено).
    """
    kwargs = _passthrough_kwargs(cfg)
    if cfg.api_base is not None:
        kwargs["base_url"] = cfg.api_base  # без /v1 — нативный эндпоинт ollama
    return ChatOllamaWithExtraSampling(model=cfg.model, **kwargs)


def _build_via_init(cfg: AIConfig) -> BaseChatModel:
    """openai/groq/llamacpp → `init_chat_model` (OpenAI-совместимые: base_url+api_key)."""
    model_provider = _PROVIDER_TO_LANGCHAIN.get(cfg.provider, cfg.provider)
    kwargs = _passthrough_kwargs(cfg)
    if cfg.api_key is not None:
        kwargs["api_key"] = cfg.api_key
    if cfg.api_base is not None:
        kwargs["base_url"] = cfg.api_base
    kwargs["timeout"] = (
        cfg.timeout_s if cfg.timeout_s is not None else _DEFAULT_TIMEOUT_S
    )
    kwargs.setdefault("max_retries", 0)  # ретраями владеет langgraph, не клиент
    return init_chat_model(cfg.model, model_provider=model_provider, **kwargs)
