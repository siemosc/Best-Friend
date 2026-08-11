"""Контракт ai-embeddings factory: маппинг config-dict → kwargs конструктора Embeddings.

openai-совместимый путь проверяется спаем (точный маппинг флагов/model_kwargs),
ollama-путь — реальной ленивой сборкой (фильтр по model_fields против extra=forbid).
Без сети.
"""

from typing import Any

from langchain_core.embeddings import Embeddings
from langchain_ollama import OllamaEmbeddings
from langchain_openai import OpenAIEmbeddings
import pytest

from bestfiend.ai.embeddings import build_embeddings


class _Spy:
    """Спай конструктора: запоминает args/kwargs, отдаёт маркер вместо объекта."""

    def __init__(self) -> None:
        self.args: tuple[Any, ...] = ()
        self.kwargs: dict[str, Any] = {}

    def __call__(self, *args: Any, **kwargs: Any) -> str:
        self.args = args
        self.kwargs = kwargs
        return "EMBEDDINGS"


@pytest.fixture
def openai_emb_spy(monkeypatch: pytest.MonkeyPatch) -> _Spy:
    """Подменяет OpenAIEmbeddings в factory спаем."""
    spy = _Spy()
    monkeypatch.setattr("bestfiend.ai.embeddings.factory.OpenAIEmbeddings", spy)
    return spy


def test_openrouter_zips_base_url_and_flags(openai_emb_spy: _Spy) -> None:
    """openrouter: base_url зашит, ctx-check off, encoding_format в model_kwargs."""
    build_embeddings(
        {
            "provider": "openrouter",
            "model": "qwen/qwen3-embedding",
            "api_key": "k",
            "timeout_s": 30,
            "dimensions": 1024,
        }
    )
    assert openai_emb_spy.kwargs == {
        "model": "qwen/qwen3-embedding",
        "dimensions": 1024,
        "model_kwargs": {"encoding_format": "float"},
        "check_embedding_ctx_length": False,
        "base_url": "https://openrouter.ai/api/v1",
        "api_key": "k",
        "timeout": 30.0,
        "max_retries": 0,
    }


def test_openai_compatible_uses_config_api_base(openai_emb_spy: _Spy) -> None:
    """openai/llamacpp: base_url берётся из api_base (не зашитый openrouter-эндпоинт)."""
    build_embeddings(
        {
            "provider": "openai",
            "model": "text-embedding-3-small",
            "api_key": "k",
            "api_base": "http://x/v1",
        }
    )
    assert openai_emb_spy.kwargs["base_url"] == "http://x/v1"


def test_explicit_ctx_check_not_overridden(openai_emb_spy: _Spy) -> None:
    """check_embedding_ctx_length из конфига уважается (setdefault, для настоящего OpenAI)."""
    build_embeddings(
        {
            "provider": "openai",
            "model": "text-embedding-3-small",
            "check_embedding_ctx_length": True,
        }
    )
    assert openai_emb_spy.kwargs["check_embedding_ctx_length"] is True


def test_explicit_encoding_format_not_overridden(openai_emb_spy: _Spy) -> None:
    """encoding_format из конфигового model_kwargs не перетирается дефолтом."""
    build_embeddings(
        {
            "provider": "openrouter",
            "model": "qwen/qwen3-embedding",
            "model_kwargs": {"encoding_format": "base64"},
        }
    )
    assert openai_emb_spy.kwargs["model_kwargs"] == {"encoding_format": "base64"}


def test_ollama_builds_and_filters_unknown_fields() -> None:
    """ollama: валидные поля проходят, чужие (presence_penalty/min_p) отсеяны до конструктора."""
    emb = build_embeddings(
        {
            "provider": "ollama",
            "model": "nomic-embed-text",
            "api_base": "http://localhost:11434",
            "dimensions": 768,
            "presence_penalty": 1.5,
            "min_p": 0.0,
        }
    )
    assert isinstance(emb, OllamaEmbeddings)
    assert emb.model == "nomic-embed-text"
    assert emb.base_url == "http://localhost:11434"
    assert emb.dimensions == 768


def test_openrouter_real_build_is_openai_embeddings() -> None:
    """Реальная ленивая сборка openrouter: наши kwargs принимает OpenAIEmbeddings."""
    emb = build_embeddings(
        {"provider": "openrouter", "model": "qwen/qwen3-embedding", "api_key": "k"}
    )
    assert isinstance(emb, OpenAIEmbeddings)
    assert isinstance(emb, Embeddings)
    assert emb.check_embedding_ctx_length is False


@pytest.mark.parametrize("config", [{"model": "x"}, {"provider": "openrouter"}, {}])
def test_requires_provider_and_model(config: dict[str, Any]) -> None:
    """Пустой/неполный provider|model → ValueError (fail-fast через AIConfig)."""
    with pytest.raises(ValueError):
        build_embeddings(config)
