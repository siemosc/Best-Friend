"""Модели для базовых и потоковых graph-тестов."""

from collections.abc import AsyncIterator
from typing import Any

from langchain_core.callbacks import (
    AsyncCallbackManagerForLLMRun,
    CallbackManagerForLLMRun,
)
from langchain_core.language_models import BaseChatModel
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage
from langchain_core.messages.tool import tool_call_chunk
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
import orjson
from pydantic import Field


class BindableFakeChatModel(GenericFakeChatModel):
    """GenericFakeChatModel + bind_tools (base кидает NotImplementedError).

    bind_tools игнорит тулы и возвращает себя — модель отдаёт скриптованные
    AIMessage по очереди (через `.ainvoke`/`.astream` контента).
    """

    def bind_tools(self, tools: Any, *, tool_choice: Any = None, **kwargs: Any) -> Any:
        return self


def bindable_chat_model(messages: list[AIMessage]) -> BindableFakeChatModel:
    """Fake-модель, отдающая `messages` по очереди на каждый `ainvoke`."""
    return BindableFakeChatModel(messages=iter(messages))


class StreamingToolFakeChatModel(BaseChatModel):
    """Fake, чей `astream` отдаёт content-чанк + (опц.) `tool_call_chunk`.

    `GenericFakeChatModel` не стримит современные `tool_calls`, поэтому для
    теста аккумуляции чанков + детекта tool_call в dispatcher нужен этот fake.
    """

    answer: str = ""
    tool_name: str | None = None
    tool_args_json: str = "{}"

    async def _astream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        if self.answer:
            yield ChatGenerationChunk(message=AIMessageChunk(content=self.answer))
        if self.tool_name is not None:
            yield ChatGenerationChunk(
                message=AIMessageChunk(
                    content="",
                    tool_call_chunks=[
                        tool_call_chunk(
                            name=self.tool_name,
                            args=self.tool_args_json,
                            id="call-1",
                            index=0,
                        )
                    ],
                )
            )

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        return ChatResult(
            generations=[ChatGeneration(message=AIMessage(content=self.answer))]
        )

    @property
    def _llm_type(self) -> str:
        return "streaming-tool-fake"

    def bind_tools(self, tools: Any, *, tool_choice: Any = None, **kwargs: Any) -> Any:
        return self


def streaming_tool_model(
    answer: str = "",
    tool_name: str | None = None,
    tool_args: dict[str, Any] | None = None,
) -> StreamingToolFakeChatModel:
    """Fake, чей `astream` стримит `answer` и (опц.) вызов `tool_name(tool_args)`."""
    return StreamingToolFakeChatModel(
        answer=answer,
        tool_name=tool_name,
        tool_args_json=orjson.dumps(tool_args or {}).decode(),
    )


class CapturingStreamFakeChatModel(BaseChatModel):
    """Fake: пишет входные `messages` каждого вызова в `.captured`, отдаёт фикс. ответ.

    Для проверки сборки промпта (что реально уходит в модель): top-level идёт
    через `astream`, subagent — через `ainvoke`/`_generate`. Читать `.captured`
    после вызова — pydantic копирует list на инстанс, внешний sink не сработает.
    """

    captured: list[BaseMessage] = Field(default_factory=list)
    answer: str = "готово"

    model_config = {"arbitrary_types_allowed": True}

    async def _astream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        self.captured.extend(messages)
        yield ChatGenerationChunk(message=AIMessageChunk(content=self.answer))

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        self.captured.extend(messages)
        return ChatResult(
            generations=[ChatGeneration(message=AIMessage(content=self.answer))]
        )

    @property
    def _llm_type(self) -> str:
        return "capturing-stream-fake"

    def bind_tools(self, tools: Any, *, tool_choice: Any = None, **kwargs: Any) -> Any:
        return self


def capturing_stream_model() -> CapturingStreamFakeChatModel:
    """Fake, пишущий входные `messages` в `.captured` (проверка сборки промпта)."""
    return CapturingStreamFakeChatModel()
