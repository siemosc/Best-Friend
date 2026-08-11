"""Smoke: ai-factory реально собирает BaseChatModel на каждый provider-path.

Без сети — конструкторы langchain ленивы. Проверяет, что провайдер-пакеты
установлены и маппинг даёт валидную модель нужного класса.
"""

from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_openrouter import ChatOpenRouter
import pytest

from bestfiend.ai.llm import build_chat_model


@pytest.mark.parametrize(
    "config",
    [
        {
            "provider": "openai",
            "model": "gpt-4o-mini",
            "api_key": "sk-test",
            "api_base": "https://api.openai.com/v1",
        },
        {"provider": "groq", "model": "llama-3.1-8b-instant", "api_key": "gsk-test"},
        {"provider": "ollama", "model": "llama3", "api_base": "http://localhost:11434"},
        {
            "provider": "llamacpp",
            "model": "local-model",
            "api_key": "not-needed",
            "api_base": "http://localhost:8080/v1",
        },
    ],
)
def test_build_returns_base_chat_model(config: dict[str, Any]) -> None:
    """Каждый init_chat_model-провайдер строит инстанс BaseChatModel."""
    model = build_chat_model(config)
    assert isinstance(model, BaseChatModel)


def test_openrouter_returns_chat_openrouter() -> None:
    """openrouter строит ChatOpenRouter с нативным openrouter_provider."""
    model = build_chat_model(
        {
            "provider": "openrouter",
            "model": "openai/gpt-4o-mini",
            "api_key": "sk-or-test",
            "extra_body": {"provider": {"order": ["OpenAI"]}},
        }
    )
    assert isinstance(model, ChatOpenRouter)
    assert model.openrouter_provider == {"order": ["OpenAI"]}
