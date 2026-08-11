"""Публичные test doubles graph, сгруппированные по production-ролям."""

from tests.graph.fakes.chat_models import (
    BindableFakeChatModel,
    CapturingStreamFakeChatModel,
    StreamingToolFakeChatModel,
    bindable_chat_model,
    capturing_stream_model,
    streaming_tool_model,
)
from tests.graph.fakes.error_models import (
    RaiseFirstAstreamThenStreamChatModel,
    RaiseThenStreamChatModel,
    RaisingChatModel,
    raise_first_astream_then_stream_model,
    raise_then_stream_model,
    raising_model,
)
from tests.graph.fakes.scripted_models import (
    ScriptedStreamingFakeChatModel,
    ScriptedToolCall,
    ScriptedTurn,
    scripted_streaming_model,
)
from tests.graph.fakes.stream_publisher import StreamPublisherFake
from tests.graph.fakes.tools import artifact_tool, echo_tool, raising_tool


__all__ = [
    "BindableFakeChatModel",
    "CapturingStreamFakeChatModel",
    "RaiseFirstAstreamThenStreamChatModel",
    "RaiseThenStreamChatModel",
    "RaisingChatModel",
    "ScriptedStreamingFakeChatModel",
    "ScriptedToolCall",
    "ScriptedTurn",
    "StreamPublisherFake",
    "StreamingToolFakeChatModel",
    "artifact_tool",
    "capturing_stream_model",
    "echo_tool",
    "bindable_chat_model",
    "raise_first_astream_then_stream_model",
    "raise_then_stream_model",
    "raising_model",
    "raising_tool",
    "scripted_streaming_model",
    "streaming_tool_model",
]
