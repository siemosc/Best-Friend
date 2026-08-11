"""Тесты react-ноды: tool-роутинг, plain-text финал, soft-gate, bind по глубине, streaming."""

from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.graph import END
from langgraph.runtime import Runtime
import pytest

from bestfiend.graph.config import MAX_RECURSION_DEPTH_DEFAULT
from bestfiend.graph.context import GraphContext
from bestfiend.graph.nodes.react.delegate_tool import DELEGATE_SUBTASK_NAME
from bestfiend.graph.nodes.react.node import react_node
from bestfiend.graph.state import InputContext, OrchestrationState, RenderedPrompts
from bestfiend.graph.stream_keys import (
    ANSWER_DELTA_KEY,
    ANSWER_RESET_KEY,
    PROGRESS_STEP_KEY,
)
from tests.graph.fakes import (
    BindableFakeChatModel,
    ScriptedToolCall,
    ScriptedTurn,
    bindable_chat_model,
    capturing_stream_model,
    echo_tool,
    raising_model,
    scripted_streaming_model,
)


def _state(**overrides: Any) -> OrchestrationState:
    base: dict[str, Any] = {
        "input": InputContext(message="найди погоду", request_id="r1"),
        "task_for_react": "найди погоду",
        "prompts": RenderedPrompts(environment="ENV", user_instruction="WORK"),
    }
    base.update(overrides)
    return OrchestrationState(**base)


def _tool_call(name: str) -> dict[str, Any]:
    return {"name": name, "args": {"q": "погода"}, "id": "c1", "type": "tool_call"}


def _recording_model(
    messages: list[AIMessage], sink: list[str]
) -> BindableFakeChatModel:
    """Fake, фиксирующий имена тулов из bind_tools в `sink`."""

    class _Recording(BindableFakeChatModel):
        def bind_tools(self, tools: Any, *, tool_choice: Any = None, **kw: Any) -> Any:
            sink.extend(getattr(t, "name", "") for t in tools)
            return self

    return _Recording(messages=iter(messages))


@pytest.mark.asyncio
async def test_react_routes_to_tools_on_tool_call() -> None:
    """top-level через astream: tool_call_chunks → tools, AIMessage с tool_calls в stm."""
    model = scripted_streaming_model(
        ScriptedTurn(tool_calls=[ScriptedToolCall(name="search")])
    )
    captured: list[Any] = []
    cmd = await react_node(
        _state(),
        Runtime(context=GraphContext(model=model), stream_writer=captured.append),
    )

    assert cmd.goto == "tools"
    update = cmd.update
    assert update is not None
    history = update["stm"]
    assert isinstance(history[0], HumanMessage)
    assert history[0].content == "найди погоду"
    assert not any(isinstance(m, SystemMessage) for m in history)
    last = history[-1]
    assert isinstance(last, AIMessage)
    assert last.tool_calls and last.tool_calls[0]["name"] == "search"


@pytest.mark.asyncio
async def test_react_plain_text_is_final_answer() -> None:
    """plain text без tool_calls → финальный ответ в `result`, goto END."""
    model = scripted_streaming_model(ScriptedTurn(content="готово"))
    captured: list[Any] = []
    cmd = await react_node(
        _state(),
        Runtime(context=GraphContext(model=model), stream_writer=captured.append),
    )

    assert cmd.goto == END
    update = cmd.update
    assert update is not None
    assert update["result"] == "готово"


@pytest.mark.asyncio
async def test_react_streams_answer_deltas_top_level() -> None:
    """content-only top-level: writer получает ANSWER_DELTA_KEY с полным текстом."""
    model = scripted_streaming_model(ScriptedTurn(content="привет, мир"))
    captured: list[dict[str, Any]] = []
    await react_node(
        _state(),
        Runtime(context=GraphContext(model=model), stream_writer=captured.append),
    )

    deltas = [c[ANSWER_DELTA_KEY] for c in captured if ANSWER_DELTA_KEY in c]
    assert "".join(deltas) == "привет, мир"
    # Чистый финал без tool_calls — reset не эмитится.
    assert not any(ANSWER_RESET_KEY in c for c in captured)


@pytest.mark.asyncio
async def test_react_progress_step_on_tool_call() -> None:
    """tool_call → PROGRESS_STEP_KEY с именем тула, ANSWER_DELTA_KEY отсутствует."""
    model = scripted_streaming_model(
        ScriptedTurn(tool_calls=[ScriptedToolCall(name="search")])
    )
    captured: list[dict[str, Any]] = []
    await react_node(
        _state(),
        Runtime(context=GraphContext(model=model), stream_writer=captured.append),
    )

    progress = [c[PROGRESS_STEP_KEY] for c in captured if PROGRESS_STEP_KEY in c]
    assert progress == ["вызываю search"]
    assert not any(ANSWER_DELTA_KEY in c for c in captured)


@pytest.mark.asyncio
async def test_react_tool_call_index_zero_emits_progress() -> None:
    """Защита от truthy-bug: index=0 валиден, ProgressStep эмитится."""
    model = scripted_streaming_model(
        ScriptedTurn(tool_calls=[ScriptedToolCall(name="search", index=0)])
    )
    captured: list[dict[str, Any]] = []
    await react_node(
        _state(),
        Runtime(context=GraphContext(model=model), stream_writer=captured.append),
    )

    progress = [c[PROGRESS_STEP_KEY] for c in captured if PROGRESS_STEP_KEY in c]
    assert progress == ["вызываю search"]


@pytest.mark.asyncio
async def test_react_parallel_tool_calls_emit_per_index() -> None:
    """Два параллельных tool_calls (index 0, 1) → две ProgressStep в порядке index."""
    model = scripted_streaming_model(
        ScriptedTurn(
            tool_calls=[
                ScriptedToolCall(name="search", index=0, id="c1"),
                ScriptedToolCall(name="fetch", index=1, id="c2"),
            ]
        )
    )
    captured: list[dict[str, Any]] = []
    await react_node(
        _state(),
        Runtime(context=GraphContext(model=model), stream_writer=captured.append),
    )

    progress = [c[PROGRESS_STEP_KEY] for c in captured if PROGRESS_STEP_KEY in c]
    assert progress == ["вызываю search", "вызываю fetch"]


@pytest.mark.asyncio
async def test_react_duplicate_args_chunks_no_duplicate_progress() -> None:
    """Доп. args-only чанки с тем же index не дублируют ProgressStep."""
    model = scripted_streaming_model(
        ScriptedTurn(
            tool_calls=[ScriptedToolCall(name="search", args='{"q":', index=0)],
            duplicate_arg_chunks=['"погода"}'],
        )
    )
    captured: list[dict[str, Any]] = []
    await react_node(
        _state(),
        Runtime(context=GraphContext(model=model), stream_writer=captured.append),
    )

    progress = [c[PROGRESS_STEP_KEY] for c in captured if PROGRESS_STEP_KEY in c]
    assert progress == ["вызываю search"]


@pytest.mark.asyncio
async def test_react_mixed_chunk_content_and_tool_call() -> None:
    """Mixed-chunk (content+tool в одном): content→AnswerDelta, затем preface-лог + reset + вызов."""
    model = scripted_streaming_model(
        ScriptedTurn(
            content="ща",
            tool_calls=[ScriptedToolCall(name="search")],
            mixed_chunk=True,
        )
    )
    captured: list[dict[str, Any]] = []
    await react_node(
        _state(),
        Runtime(context=GraphContext(model=model), stream_writer=captured.append),
    )

    keys_in_order = [next(iter(c)) for c in captured]
    assert keys_in_order == [
        ANSWER_DELTA_KEY,
        PROGRESS_STEP_KEY,
        ANSWER_RESET_KEY,
        PROGRESS_STEP_KEY,
    ]
    assert captured[0][ANSWER_DELTA_KEY] == "ща"
    assert captured[1][PROGRESS_STEP_KEY] == "ща"  # preface зафиксирован в лог
    assert captured[3][PROGRESS_STEP_KEY] == "вызываю search"


@pytest.mark.asyncio
async def test_react_preface_before_tool_emits_log_and_reset() -> None:
    """preface (раздельные чанки) перед tool → AnswerDelta, затем preface-лог, reset, вызов."""
    model = scripted_streaming_model(
        ScriptedTurn(
            content="сейчас поищу",
            tool_calls=[ScriptedToolCall(name="search")],
        )
    )
    captured: list[dict[str, Any]] = []
    await react_node(
        _state(),
        Runtime(context=GraphContext(model=model), stream_writer=captured.append),
    )

    keys_in_order = [next(iter(c)) for c in captured]
    assert keys_in_order == [
        ANSWER_DELTA_KEY,
        PROGRESS_STEP_KEY,
        ANSWER_RESET_KEY,
        PROGRESS_STEP_KEY,
    ]
    assert captured[0][ANSWER_DELTA_KEY] == "сейчас поищу"
    assert captured[1][PROGRESS_STEP_KEY] == "сейчас поищу"
    assert captured[3][PROGRESS_STEP_KEY] == "вызываю search"


@pytest.mark.asyncio
async def test_react_empty_preface_tool_resets_without_log() -> None:
    """tool без content → reset эмитится, preface-ProgressStep нет, AnswerDelta нет."""
    model = scripted_streaming_model(
        ScriptedTurn(tool_calls=[ScriptedToolCall(name="search")])
    )
    captured: list[dict[str, Any]] = []
    await react_node(
        _state(),
        Runtime(context=GraphContext(model=model), stream_writer=captured.append),
    )

    assert any(ANSWER_RESET_KEY in c for c in captured)
    assert not any(ANSWER_DELTA_KEY in c for c in captured)
    progress = [c[PROGRESS_STEP_KEY] for c in captured if PROGRESS_STEP_KEY in c]
    assert progress == ["вызываю search"]


@pytest.mark.asyncio
async def test_react_stream_break_after_deltas_emits_reset() -> None:
    """Обрыв стрима после видимых дельт → ANSWER_RESET перед re-raise.

    Иначе retry настримит дубль поверх обрубка, а error-нода допишет к нему.
    """
    model = scripted_streaming_model(
        ScriptedTurn(content="сейчас рас", raise_after=RuntimeError("stream broke"))
    )
    captured: list[dict[str, Any]] = []
    with pytest.raises(RuntimeError, match="stream broke"):
        await react_node(
            _state(),
            Runtime(context=GraphContext(model=model), stream_writer=captured.append),
        )

    keys_in_order = [next(iter(c)) for c in captured]
    assert keys_in_order == [ANSWER_DELTA_KEY, ANSWER_RESET_KEY]


@pytest.mark.asyncio
async def test_react_stream_break_before_deltas_no_reset() -> None:
    """Обрыв до первой дельты → сбрасывать нечего, ANSWER_RESET не эмитится."""
    model = raising_model(RuntimeError("stream broke"))
    captured: list[dict[str, Any]] = []
    with pytest.raises(RuntimeError, match="stream broke"):
        await react_node(
            _state(),
            Runtime(context=GraphContext(model=model), stream_writer=captured.append),
        )

    assert captured == []


@pytest.mark.asyncio
async def test_react_stream_break_after_tool_start_no_second_reset() -> None:
    """Обрыв после сворота в tool_calls → второй ANSWER_RESET не эмитится.

    Дельты после сворота не идут (гейт tool_started), накопитель consumer'а
    уже сброшен reset'ом preface-логики — обрыв не оставляет обрубка.
    """
    model = scripted_streaming_model(
        ScriptedTurn(
            content="ща поищу",
            tool_calls=[ScriptedToolCall(name="search")],
            raise_after=RuntimeError("stream broke"),
        )
    )
    captured: list[dict[str, Any]] = []
    with pytest.raises(RuntimeError, match="stream broke"):
        await react_node(
            _state(),
            Runtime(context=GraphContext(model=model), stream_writer=captured.append),
        )

    resets = [c for c in captured if ANSWER_RESET_KEY in c]
    assert len(resets) == 1


@pytest.mark.asyncio
async def test_react_skips_progress_for_delegate_subtask() -> None:
    """delegate_subtask — внутренний tool, ProgressStep НЕ эмитим."""
    model = scripted_streaming_model(
        ScriptedTurn(tool_calls=[ScriptedToolCall(name=DELEGATE_SUBTASK_NAME)])
    )
    captured: list[dict[str, Any]] = []
    await react_node(
        _state(),
        Runtime(context=GraphContext(model=model), stream_writer=captured.append),
    )

    assert not any(PROGRESS_STEP_KEY in c for c in captured)


@pytest.mark.asyncio
async def test_react_subagent_does_not_stream() -> None:
    """Subagent path использует ainvoke (без стрима) — captured пустой."""
    model = bindable_chat_model(
        [AIMessage(content="", tool_calls=[_tool_call("search")])]
    )
    captured: list[Any] = []
    cmd = await react_node(
        _state(processing_mode="subagent"),
        Runtime(context=GraphContext(model=model), stream_writer=captured.append),
    )

    assert cmd.goto == "tools"
    assert captured == []


@pytest.mark.asyncio
async def test_react_top_level_soft_gate_routes_to_error() -> None:
    """top-level (task) soft-gate → error + loop_exhausted."""
    model = bindable_chat_model([AIMessage(content="не должно вызваться")])
    cmd = await react_node(
        _state(remaining_steps=2), Runtime(context=GraphContext(model=model))
    )

    assert cmd.goto == "error"
    update = cmd.update
    assert update is not None
    assert update["error_signal"].kind == "loop_exhausted"
    assert update["error_signal"].node == "react"


@pytest.mark.asyncio
async def test_react_subagent_soft_gate_summarizes_to_result() -> None:
    """subagent soft-gate → summarize, итог в `result`, goto END (без error)."""
    model = bindable_chat_model([AIMessage(content="сводка по собранному")])
    state = _state(remaining_steps=2, processing_mode="subagent")
    cmd = await react_node(state, Runtime(context=GraphContext(model=model)))

    assert cmd.goto == END
    update = cmd.update
    assert update is not None
    assert update["result"] == "сводка по собранному"


@pytest.mark.asyncio
async def test_react_binds_delegate_below_max_depth() -> None:
    """recursion_depth < MAX → delegate_subtask забинжена."""
    sink: list[str] = []
    model = _recording_model([AIMessage(content="done")], sink)
    await react_node(
        _state(recursion_depth=0, processing_mode="subagent"),
        Runtime(context=GraphContext(model=model)),
    )

    assert DELEGATE_SUBTASK_NAME in sink


@pytest.mark.asyncio
async def test_react_no_delegate_at_max_depth() -> None:
    """recursion_depth == MAX → delegate_subtask НЕ биндим."""
    sink: list[str] = []
    model = _recording_model([AIMessage(content="done")], sink)
    await react_node(
        _state(recursion_depth=MAX_RECURSION_DEPTH_DEFAULT, processing_mode="subagent"),
        Runtime(context=GraphContext(model=model)),
    )

    assert DELEGATE_SUBTASK_NAME not in sink


def _memory_tools_context(model: Any) -> GraphContext:
    """Контекст с тулзами памяти, помеченными top-level-only."""
    return GraphContext(
        model=model,
        tools_by_name={
            "memory_search": echo_tool("memory_search", "найдено"),
            "memory_save": echo_tool("memory_save", "сохранено"),
            "web_search": echo_tool("web_search", "результат"),
        },
        top_level_only_tool_names=frozenset({"memory_search", "memory_save"}),
    )


@pytest.mark.asyncio
async def test_react_top_level_binds_memory_tools() -> None:
    """Top-level видит тулзы памяти вместе с обычными."""
    sink: list[str] = []
    model = _recording_model([AIMessage(content="done")], sink)
    state = _state(stm=[HumanMessage(content="q")], turn_start_index=0)

    await react_node(
        state,
        Runtime(context=_memory_tools_context(model), stream_writer=lambda _: None),
    )

    assert "memory_search" in sink
    assert "memory_save" in sink
    assert "web_search" in sink


@pytest.mark.asyncio
async def test_react_subagent_hides_memory_tools() -> None:
    """Subagent не видит top-level-only тулзы; обычные — видит."""
    sink: list[str] = []
    model = _recording_model([AIMessage(content="done")], sink)

    await react_node(
        _state(processing_mode="subagent"),
        Runtime(context=_memory_tools_context(model)),
    )

    assert "memory_search" not in sink
    assert "memory_save" not in sink
    assert "web_search" in sink


@pytest.mark.asyncio
async def test_react_top_level_enriches_human_in_messages_not_stm() -> None:
    """Top-level: Human в messages модели обогащён runtime-контекстом; stm остаётся чистым."""
    user = HumanMessage(content="найди погоду")
    model = capturing_stream_model()
    state = _state(
        stm=[user],
        turn_start_index=0,
        prompts=RenderedPrompts(
            environment="ENV", memory_recall="MEM", user_instruction="WORK"
        ),
    )
    await react_node(
        state,
        Runtime(context=GraphContext(model=model), stream_writer=lambda _: None),
    )

    # В модель ушёл обогащённый Human: время + память + исходный текст запроса.
    humans = [m for m in model.captured if isinstance(m, HumanMessage)]
    assert (
        humans[0].content
        == "<system-reminder>\nENV\n\nMEM\n</system-reminder>\n\nнайди погоду"
    )
    # stm в state НЕ мутирован — обогащение живёт только в локальной копии.
    assert state.stm[0] is user
    assert state.stm[0].content == "найди погоду"


@pytest.mark.asyncio
async def test_react_subagent_does_not_enrich_task() -> None:
    """Subagent: Human(task_for_react) уходит в модель без runtime-префикса."""
    model = capturing_stream_model()
    state = _state(
        processing_mode="subagent",
        work_history=[HumanMessage(content="под-задача")],
        prompts=RenderedPrompts(environment="ENV", memory_recall="MEM"),
    )
    await react_node(state, Runtime(context=GraphContext(model=model)))

    humans = [m for m in model.captured if isinstance(m, HumanMessage)]
    assert humans[0].content == "под-задача"


@pytest.mark.asyncio
async def test_react_top_level_enrich_preserves_image_blocks() -> None:
    """Гидрированный Human: runtime-контекст вклеивается в text-блок, картинки целы."""
    user = HumanMessage(
        content=[
            {"type": "text", "text": "что на фото?"},
            {"type": "image", "base64": "aGk=", "mime_type": "image/jpeg"},
        ]
    )
    model = capturing_stream_model()
    state = _state(
        stm=[user],
        turn_start_index=0,
        prompts=RenderedPrompts(
            environment="ENV", memory_recall="MEM", user_instruction="WORK"
        ),
    )
    await react_node(
        state,
        Runtime(context=GraphContext(model=model), stream_writer=lambda _: None),
    )

    humans = [m for m in model.captured if isinstance(m, HumanMessage)]
    blocks = humans[0].content
    assert isinstance(blocks, list)
    assert blocks[0] == {
        "type": "text",
        "text": "<system-reminder>\nENV\n\nMEM\n</system-reminder>\n\nчто на фото?",
    }
    assert blocks[1] == {"type": "image", "base64": "aGk=", "mime_type": "image/jpeg"}
    # stm не мутирован — обогащение живёт только в локальной копии.
    assert state.stm[0] is user
