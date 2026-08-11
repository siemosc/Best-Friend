"""Контракт ai-factory: маппинг config-dict → kwargs конструктора модели.

Проверяет ЛОГИКУ маппинга (provider→model_provider, api_base→base_url,
extra_body.provider→openrouter_provider, drop bind-time-полей), подменяя
конструкторы спаями — без реальной сборки и сети.
"""

from typing import Any

from langchain_core.messages import HumanMessage
import pytest

from bestfiend.ai.llm import build_chat_model
from bestfiend.ai.llm.ollama import ChatOllamaWithExtraSampling


class _Spy:
    """Спай конструктора: запоминает args/kwargs, отдаёт маркер вместо модели."""

    def __init__(self) -> None:
        self.args: tuple[Any, ...] = ()
        self.kwargs: dict[str, Any] = {}

    def __call__(self, *args: Any, **kwargs: Any) -> str:
        self.args = args
        self.kwargs = kwargs
        return "MODEL"


@pytest.fixture
def init_spy(monkeypatch: pytest.MonkeyPatch) -> _Spy:
    """Подменяет init_chat_model в factory спаем."""
    spy = _Spy()
    monkeypatch.setattr("bestfiend.ai.llm.factory.init_chat_model", spy)
    return spy


@pytest.fixture
def openrouter_spy(monkeypatch: pytest.MonkeyPatch) -> _Spy:
    """Подменяет ChatOpenRouter в factory спаем."""
    spy = _Spy()
    monkeypatch.setattr("bestfiend.ai.llm.factory.ChatOpenRouter", spy)
    return spy


@pytest.fixture
def ollama_spy(monkeypatch: pytest.MonkeyPatch) -> _Spy:
    """Подменяет ChatOllamaWithExtraSampling в factory спаем."""
    spy = _Spy()
    monkeypatch.setattr("bestfiend.ai.llm.factory.ChatOllamaWithExtraSampling", spy)
    return spy


def test_openai_maps_base_url_provider_timeout(init_spy: _Spy) -> None:
    """openai: provider→model_provider, api_base→base_url, timeout_s→timeout."""
    build_chat_model(
        {
            "provider": "openai",
            "model": "gpt-4o-mini",
            "api_key": "k",
            "api_base": "http://x/v1",
            "timeout_s": 30,
            "temperature": 0.2,
        }
    )
    assert init_spy.args == ("gpt-4o-mini",)
    assert init_spy.kwargs == {
        "model_provider": "openai",
        "temperature": 0.2,
        "api_key": "k",
        "base_url": "http://x/v1",
        "timeout": 30.0,
        "max_retries": 0,
    }


def test_llamacpp_maps_to_openai_provider(init_spy: _Spy) -> None:
    """llamacpp → model_provider='openai' (OpenAI-совместимый протокол)."""
    build_chat_model(
        {
            "provider": "llamacpp",
            "model": "local",
            "api_base": "http://localhost:8080/v1",
        }
    )
    assert init_spy.kwargs["model_provider"] == "openai"
    assert init_spy.kwargs["base_url"] == "http://localhost:8080/v1"


def test_groq_provider_passthrough(init_spy: _Spy) -> None:
    """groq: model_provider пробрасывается как есть."""
    build_chat_model(
        {"provider": "groq", "model": "llama-3.1-8b-instant", "api_key": "k"}
    )
    assert init_spy.kwargs["model_provider"] == "groq"


def test_openrouter_extracts_provider_routing(openrouter_spy: _Spy) -> None:
    """openrouter: extra_body.provider → openrouter_provider; base_url не идёт."""
    build_chat_model(
        {
            "provider": "openrouter",
            "model": "openai/gpt-4o",
            "api_key": "k",
            "api_base": "https://openrouter.ai/api/v1",
            "timeout_s": 30,
            "temperature": 0.2,
            "extra_body": {"provider": {"order": ["alibaba"]}},
        }
    )
    assert (
        openrouter_spy.kwargs
        == {
            "model": "openai/gpt-4o",
            "openrouter_provider": {"order": ["alibaba"]},
            "temperature": 0.2,
            "api_key": "k",
            "timeout": 30000,  # секунды → миллисекунды (ChatOpenRouter.timeout в мс)
            "max_retries": 0,
            "app_url": None,  # гасим attribution-заголовки → SDK свой клиент (таймаут работает)
            "app_title": None,
        }
    )


def test_openrouter_maps_native_extra_body_keys(openrouter_spy: _Spy) -> None:
    """openrouter: known-ключи extra_body (reasoning) → одноимённые нативные поля."""
    build_chat_model(
        {
            "provider": "openrouter",
            "model": "openai/gpt-4o",
            "extra_body": {
                "provider": {"order": ["alibaba"]},
                "reasoning": {"effort": "low"},
            },
        }
    )
    assert openrouter_spy.kwargs["openrouter_provider"] == {"order": ["alibaba"]}
    assert openrouter_spy.kwargs["reasoning"] == {"effort": "low"}
    assert "extra_body" not in openrouter_spy.kwargs


def test_openrouter_normalizes_legacy_reasoning_disabled(openrouter_spy: _Spy) -> None:
    """openrouter: легаси reasoning {'enabled': False} → {'effort': 'none'}.

    Схема openrouter-SDK знает только effort/summary: 'enabled' молча отрезался
    бы сериализатором и thinking остался бы включён (конфиги *-nothink).
    """
    build_chat_model(
        {
            "provider": "openrouter",
            "model": "deepseek/deepseek-v4-flash",
            "extra_body": {
                "provider": {"order": ["alibaba"]},
                "reasoning": {"enabled": False},
            },
        }
    )
    assert openrouter_spy.kwargs["reasoning"] == {"effort": "none"}


def test_openrouter_drops_unknown_extra_body(openrouter_spy: _Spy) -> None:
    """openrouter: нераспознанный остаток extra_body дропается, не пробрасывается.

    Chat.send_async() SDK имеет строгую сигнатуру — проброс неизвестного ключа
    давал бы TypeError на каждом вызове (мёртвая модель хуже потери параметра).
    """
    build_chat_model(
        {
            "provider": "openrouter",
            "model": "openai/gpt-4o",
            "extra_body": {"provider": {"order": ["alibaba"]}, "transforms": ["x"]},
        }
    )
    assert openrouter_spy.kwargs["openrouter_provider"] == {"order": ["alibaba"]}
    assert "transforms" not in openrouter_spy.kwargs
    assert "extra_body" not in openrouter_spy.kwargs


def test_openrouter_does_not_mutate_input_config(openrouter_spy: _Spy) -> None:
    """build_chat_model не мутирует вложенный extra_body переданного config."""
    config = {
        "provider": "openrouter",
        "model": "openai/gpt-4o",
        "extra_body": {"provider": {"order": ["alibaba"]}, "transforms": ["x"]},
    }
    build_chat_model(config)
    assert config["extra_body"] == {
        "provider": {"order": ["alibaba"]},
        "transforms": ["x"],
    }


def test_ollama_maps_sampling_without_timeout_or_api_key(ollama_spy: _Spy) -> None:
    """ollama: sampling+reasoning идут в конструктор, api_base→base_url, timeout_s/api_key — нет."""
    build_chat_model(
        {
            "provider": "ollama",
            "model": "qwen3.6:35b-iq3xxs",
            "api_base": "http://ded.local:11434",
            "api_key": "ignored",
            "timeout_s": 600,
            "reasoning": True,
            "temperature": 1.0,
            "presence_penalty": 1.5,
            "min_p": 0.0,
            "num_ctx": 65536,
        }
    )
    assert ollama_spy.args == ()
    assert ollama_spy.kwargs == {
        "model": "qwen3.6:35b-iq3xxs",
        "base_url": "http://ded.local:11434",
        "reasoning": True,
        "temperature": 1.0,
        "presence_penalty": 1.5,
        "min_p": 0.0,
        "num_ctx": 65536,
    }


def test_ollama_extended_injects_penalties_into_options() -> None:
    """ChatOllamaWithExtraSampling кладёт presence_penalty/min_p в ollama options (базовый класс их теряет)."""
    model = ChatOllamaWithExtraSampling(
        model="m",
        base_url="http://x:11434",
        reasoning=True,
        presence_penalty=1.5,
        min_p=0.0,
        temperature=1.0,
        top_k=20,
    )
    params = model._chat_params([HumanMessage(content="hi")])
    options = params["options"]
    assert options["presence_penalty"] == 1.5
    assert options["min_p"] == 0.0
    assert options["temperature"] == 1.0
    assert options["top_k"] == 20
    assert params["think"] is True


def test_parallel_tool_calls_dropped(init_spy: _Spy) -> None:
    """parallel_tool_calls уходит в bind_tools, не в конструктор модели."""
    build_chat_model(
        {"provider": "openai", "model": "gpt-4o-mini", "parallel_tool_calls": True}
    )
    assert "parallel_tool_calls" not in init_spy.kwargs


@pytest.mark.parametrize("config", [{"model": "x"}, {"provider": "openai"}, {}])
def test_requires_provider_and_model(config: dict[str, Any]) -> None:
    """Пустой/неполный provider|model → ValueError (fail-fast)."""
    with pytest.raises(ValueError):
        build_chat_model(config)
