"""Сценарная потоковая модель для многошаговых graph-тестов."""

from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from typing import Any

from langchain_core.callbacks import (
    AsyncCallbackManagerForLLMRun,
    CallbackManagerForLLMRun,
)
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage
from langchain_core.messages.tool import tool_call_chunk
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
import orjson


@dataclass(slots=True)
class ScriptedToolCall:
    """Описание одного tool call внутри сценарного шага."""

    name: str
    args: str = "{}"
    id: str = "call-1"
    index: int = 0


@dataclass(slots=True)
class ScriptedTurn:
    """Один react-шаг сценарной модели."""

    content: str = ""
    tool_calls: list[ScriptedToolCall] = field(default_factory=list)
    mixed_chunk: bool = False
    duplicate_arg_chunks: list[str] = field(default_factory=list)
    raise_after: Exception | None = None


class ScriptedStreamingFakeChatModel(BaseChatModel):
    """Проигрывает последовательность сценарных шагов через потоковый API."""

    turns: Iterator[ScriptedTurn]

    model_config = {"arbitrary_types_allowed": True}

    async def _astream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        turn = next(self.turns)
        if turn.mixed_chunk:
            tool_call_chunks = [
                tool_call_chunk(
                    name=tool_call.name,
                    args=tool_call.args,
                    id=tool_call.id,
                    index=tool_call.index,
                )
                for tool_call in turn.tool_calls
            ]
            yield ChatGenerationChunk(
                message=AIMessageChunk(
                    content=turn.content,
                    tool_call_chunks=tool_call_chunks,
                )
            )
        else:
            if turn.content:
                yield ChatGenerationChunk(message=AIMessageChunk(content=turn.content))
            for tool_call in turn.tool_calls:
                yield ChatGenerationChunk(
                    message=AIMessageChunk(
                        content="",
                        tool_call_chunks=[
                            tool_call_chunk(
                                name=tool_call.name,
                                args=tool_call.args,
                                id=tool_call.id,
                                index=tool_call.index,
                            )
                        ],
                    )
                )
            if turn.duplicate_arg_chunks and turn.tool_calls:
                first_tool_call = turn.tool_calls[0]
                for extra_args in turn.duplicate_arg_chunks:
                    yield ChatGenerationChunk(
                        message=AIMessageChunk(
                            content="",
                            tool_call_chunks=[
                                tool_call_chunk(
                                    name=None,
                                    args=extra_args,
                                    id=first_tool_call.id,
                                    index=first_tool_call.index,
                                )
                            ],
                        )
                    )
        if turn.raise_after is not None:
            raise turn.raise_after

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        turn = next(self.turns)
        tool_calls = [
            {
                "name": tool_call.name,
                "args": orjson.loads(tool_call.args) if tool_call.args else {},
                "id": tool_call.id,
                "type": "tool_call",
            }
            for tool_call in turn.tool_calls
        ]
        message = AIMessage(  # pyright: ignore[reportArgumentType]
            content=turn.content,
            tool_calls=tool_calls,
        )
        return ChatResult(generations=[ChatGeneration(message=message)])

    @property
    def _llm_type(self) -> str:
        return "scripted-streaming-fake"

    def bind_tools(self, tools: Any, *, tool_choice: Any = None, **kwargs: Any) -> Any:
        return self


def scripted_streaming_model(*turns: ScriptedTurn) -> ScriptedStreamingFakeChatModel:
    """Создаёт модель, проигрывающую переданные сценарные шаги."""
    return ScriptedStreamingFakeChatModel(turns=iter(turns))
