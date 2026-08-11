"""ChatOllama с пробросом sampling-опций, не объявленных полями в langchain-ollama.

langchain-ollama (1.1.0) строит ollama `options` из фиксированного набора полей и не
знает про presence_penalty/min_p. Эталон Qwen3.6 для thinking-режима требует
presence_penalty=1.5 — без него reasoning сваливается в фатальное зацикливание
(done_reason=length, пустой ответ). Добавляем недостающие поля и домешиваем их в
`options`; один override `_chat_params` покрывает sync и async путь (оба зовут его).
"""

from typing import Any

from langchain_core.messages import BaseMessage
from langchain_ollama import ChatOllama


class ChatOllamaWithExtraSampling(ChatOllama):
    """ChatOllama + sampling-опции ollama, отсутствующие полями в базовом классе."""

    presence_penalty: float | None = None
    min_p: float | None = None

    def _chat_params(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Дополняет ollama `options` полями presence_penalty/min_p (их нет в базовом классе)."""
        params = super()._chat_params(messages, stop, **kwargs)
        options = params.get("options")
        if isinstance(options, dict):
            # setdefault: явный options из call-time kwargs имеет приоритет над полями.
            if self.presence_penalty is not None:
                options.setdefault("presence_penalty", self.presence_penalty)
            if self.min_p is not None:
                options.setdefault("min_p", self.min_p)
        return params
