"""AIConfig — passthrough dict-обёртка для LLM-параметров."""

import pytest

from bestfiend.ai.config import AIConfig
from bestfiend.ai.errors import AIConfigError


def test_minimal_config() -> None:
    cfg = AIConfig({"provider": "openrouter", "model": "qwen/x"})
    assert cfg.provider == "openrouter"
    assert cfg.model == "qwen/x"


def test_as_kwargs_excludes_client_fields() -> None:
    cfg = AIConfig(
        {
            "provider": "openrouter",
            "model": "qwen/x",
            "api_key": "sk-test",
            "api_base": "https://example.com",
            "timeout_s": 30,
            "temperature": 0.5,
        }
    )
    kwargs = cfg.as_kwargs()
    assert "provider" not in kwargs
    assert "model" not in kwargs
    assert "api_key" not in kwargs
    assert "api_base" not in kwargs
    assert "timeout_s" not in kwargs
    assert kwargs["temperature"] == 0.5


def test_as_kwargs_excludes_call_time_fields() -> None:
    cfg = AIConfig(
        {
            "provider": "groq",
            "model": "llama",
            "tools": [{"type": "function"}],
            "stream": True,
            "temperature": 0.2,
        }
    )
    kwargs = cfg.as_kwargs()
    assert "tools" not in kwargs
    assert "stream" not in kwargs
    assert kwargs["temperature"] == 0.2


def test_none_values_excluded() -> None:
    cfg = AIConfig(
        {
            "provider": "groq",
            "model": "llama",
            "temperature": 0.2,
            "max_tokens": None,
        }
    )
    kwargs = cfg.as_kwargs()
    assert "max_tokens" not in kwargs
    assert kwargs["temperature"] == 0.2


def test_passthrough_arbitrary_params() -> None:
    cfg = AIConfig(
        {
            "provider": "openrouter",
            "model": "qwen/x",
            "temperature": 0.5,
            "reasoning_effort": "high",
            "response_format": {"type": "json_object"},
            "extra_body": {"provider": {"order": ["alibaba"]}},
            "think": True,
        }
    )
    kwargs = cfg.as_kwargs()
    assert kwargs["reasoning_effort"] == "high"
    assert kwargs["response_format"] == {"type": "json_object"}
    assert kwargs["extra_body"] == {"provider": {"order": ["alibaba"]}}
    assert kwargs["think"] is True


def test_typed_properties() -> None:
    cfg = AIConfig(
        {
            "provider": "openrouter",
            "model": "qwen/x",
            "timeout_s": "30",
            "max_tokens": "4000",
            "api_key": "sk-test",
            "api_base": "https://api.example.com",
        }
    )
    assert cfg.timeout_s == 30.0
    assert cfg.max_tokens == 4000
    assert cfg.api_key == "sk-test"
    assert cfg.api_base == "https://api.example.com"


def test_optional_properties_default_none() -> None:
    cfg = AIConfig({"provider": "groq", "model": "llama"})
    assert cfg.timeout_s is None
    assert cfg.max_tokens is None
    assert cfg.api_key is None
    assert cfg.api_base is None


def test_malformed_numeric_fields_raise_ai_config_error() -> None:
    """Кривые числовые поля бросают AIConfigError (ловится fail-open), не сырой ValueError."""
    for field in ("context_window", "max_tokens", "timeout_s"):
        cfg = AIConfig({"provider": "openrouter", "model": "m", field: "not-a-number"})
        with pytest.raises(AIConfigError):
            getattr(cfg, field)


def test_missing_provider_raises() -> None:
    with pytest.raises(ValueError, match="provider"):
        AIConfig({"model": "llama"})


def test_missing_model_raises() -> None:
    with pytest.raises(ValueError, match="model"):
        AIConfig({"provider": "groq"})


def test_empty_provider_raises() -> None:
    with pytest.raises(ValueError, match="provider"):
        AIConfig({"provider": "", "model": "llama"})


def test_empty_model_raises() -> None:
    with pytest.raises(ValueError, match="model"):
        AIConfig({"provider": "groq", "model": ""})


def test_supports_vision_default_false() -> None:
    cfg = AIConfig({"provider": "openrouter", "model": "m"})
    assert cfg.supports_vision is False


def test_supports_vision_true_and_excluded_from_kwargs() -> None:
    """Флаг vision — локальные метаданные: property читается, в kwargs не утекает."""
    cfg = AIConfig({"provider": "openrouter", "model": "m", "supports_vision": True})
    assert cfg.supports_vision is True
    assert "supports_vision" not in cfg.as_kwargs()
