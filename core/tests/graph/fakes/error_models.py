"""Модели для проверки веток ошибок graph."""

from collections.abc import AsyncIterator
from typing import Any

from langchain_core.callbacks import (
    AsyncCallbackManagerForLLMRun,
    CallbackManagerForLLMRun,
)
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessageChunk, BaseMessage
from langchain_core.outputs import ChatGenerationChunk, ChatResult
from pydantic import PrivateAttr


class RaisingChatModel(BaseChatModel):
    """Модель, чьи синхронный и потоковый вызовы выбрасывают заданную ошибку."""

    error: Exception

    model_config = {"arbitrary_types_allowed": True}

    async def _astream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        raise self.error
        yield  # pragma: no cover

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        raise self.error

    @property
    def _llm_type(self) -> str:
        return "raising-fake"

    def bind_tools(self, tools: Any, *, tool_choice: Any = None, **kwargs: Any) -> Any:
        return self


def raising_model(error: Exception) -> RaisingChatModel:
    """Создаёт модель, которая на любой вызов выбрасывает `error`."""
    return RaisingChatModel(error=error)


class RaiseThenStreamChatModel(BaseChatModel):
    """Синхронный вызов падает, потоковый возвращает финальный ответ."""

    error: Exception
    answer: str = ""

    model_config = {"arbitrary_types_allowed": True}

    async def _astream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        yield ChatGenerationChunk(message=AIMessageChunk(content=self.answer))

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        raise self.error

    @property
    def _llm_type(self) -> str:
        return "raise-then-stream-fake"

    def bind_tools(self, tools: Any, *, tool_choice: Any = None, **kwargs: Any) -> Any:
        return self


def raise_then_stream_model(error: Exception, answer: str) -> RaiseThenStreamChatModel:
    """Создаёт модель с ошибкой react-вызова и успешным finalize-потоком."""
    return RaiseThenStreamChatModel(error=error, answer=answer)


class RaiseFirstAstreamThenStreamChatModel(BaseChatModel):
    """Первый потоковый вызов падает, последующие возвращают ответ."""

    error: Exception
    answer: str = ""
    _astream_calls: int = PrivateAttr(default=0)

    model_config = {"arbitrary_types_allowed": True}

    async def _astream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        self._astream_calls += 1
        if self._astream_calls == 1:
            raise self.error
            yield  # pragma: no cover
        yield ChatGenerationChunk(message=AIMessageChunk(content=self.answer))

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        raise self.error

    @property
    def _llm_type(self) -> str:
        return "raise-first-astream-then-stream-fake"

    def bind_tools(self, tools: Any, *, tool_choice: Any = None, **kwargs: Any) -> Any:
        return self


def raise_first_astream_then_stream_model(
    error: Exception,
    answer: str,
) -> RaiseFirstAstreamThenStreamChatModel:
    """Создаёт модель с первым ошибочным и последующими успешными потоками."""
    return RaiseFirstAstreamThenStreamChatModel(error=error, answer=answer)
