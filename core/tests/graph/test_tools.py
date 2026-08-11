"""Тесты tools-ноды: исполнение, delegate_subtask (рекурсия), per-call изоляция."""

import asyncio
from typing import Any, Literal
from unittest.mock import MagicMock

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.tools import StructuredTool
from langgraph.runtime import Runtime
import pytest

from bestfiend.contracts.artifacts import ArtifactRef
from bestfiend.graph import build_graph
from bestfiend.graph.attached_artifacts import hydrate_image_artifacts
from bestfiend.graph.context import GraphContext
from bestfiend.graph.nodes.react.delegate_tool import DELEGATE_SUBTASK_NAME
from bestfiend.graph.nodes.react.send_artifact_tool import SEND_ARTIFACT_TO_USER_NAME
from bestfiend.graph.nodes.tools.node import tools_node
from bestfiend.graph.state import InputContext, OrchestrationState
from tests.graph.fakes import (
    artifact_tool,
    bindable_chat_model,
    capturing_stream_model,
    echo_tool,
    raising_tool,
)


def _tc(name: str, args: dict[str, Any], call_id: str) -> dict[str, Any]:
    return {"name": name, "args": args, "id": call_id, "type": "tool_call"}


def _state_with(
    tool_calls: list[dict[str, Any]],
    *,
    processing_mode: Literal["task", "subagent"] = "task",
) -> OrchestrationState:
    """State с AIMessage(tool_calls) в активной ленте (stm для task, work_history для subagent)."""
    lane: list[BaseMessage] = [AIMessage(content="", tool_calls=tool_calls)]
    sub = processing_mode == "subagent"
    return OrchestrationState(
        input=InputContext(message="x", request_id="r1"),
        processing_mode=processing_mode,
        stm=[] if sub else lane,
        work_history=lane if sub else [],
    )


@pytest.mark.asyncio
async def test_tools_executes_and_returns_toolmessage() -> None:
    state = _state_with([_tc("search", {"q": "погода"}, "c1")])
    ctx = GraphContext(
        model=bindable_chat_model([]),
        tools_by_name={"search": echo_tool("search", "найдено")},
    )

    cmd = await tools_node(state, Runtime(context=ctx))

    assert cmd.goto == "react"
    update = cmd.update
    assert update is not None
    msgs = update["stm"]
    assert len(msgs) == 1
    assert isinstance(msgs[0], ToolMessage)
    assert msgs[0].tool_call_id == "c1"
    assert msgs[0].content == "найдено"


@pytest.mark.asyncio
async def test_tools_delegate_without_graph_errors() -> None:
    """delegate_subtask + context.graph=None → error-ToolMessage (не AttributeError)."""
    state = _state_with([_tc(DELEGATE_SUBTASK_NAME, {"task": "под-задача"}, "c1")])

    cmd = await tools_node(
        state, Runtime(context=GraphContext(model=bindable_chat_model([])))
    )

    assert cmd.goto == "react"
    update = cmd.update
    assert update is not None
    msg = update["stm"][0]
    assert msg.status == "error"
    assert "граф" in msg.content


@pytest.mark.asyncio
async def test_tools_delegate_runs_child_and_returns_result() -> None:
    """delegate_subtask → дочерний react-прогон, его `result` приходит как ToolMessage."""
    state = _state_with([_tc(DELEGATE_SUBTASK_NAME, {"task": "посчитай"}, "c1")])
    child = bindable_chat_model([AIMessage(content="результат под-задачи")])
    ctx = GraphContext(graph=build_graph(), model=child)

    cmd = await tools_node(state, Runtime(context=ctx))

    assert cmd.goto == "react"
    update = cmd.update
    assert update is not None
    msg = update["stm"][0]
    assert msg.tool_call_id == "c1"
    assert msg.content == "результат под-задачи"


@pytest.mark.asyncio
async def test_tools_per_call_isolation() -> None:
    state = _state_with([_tc("ok", {}, "c1"), _tc("bad", {}, "c2")])
    ctx = GraphContext(
        model=bindable_chat_model([]),
        tools_by_name={"ok": echo_tool("ok", "ok-result"), "bad": raising_tool("bad")},
    )

    cmd = await tools_node(state, Runtime(context=ctx))

    assert cmd.goto == "react"
    update = cmd.update
    assert update is not None
    by_id = {m.tool_call_id: m for m in update["stm"]}
    assert by_id["c1"].content == "ok-result"
    assert by_id["c1"].status == "success"
    assert by_id["c2"].status == "error"
    assert "boom" in by_id["c2"].content


@pytest.mark.asyncio
async def test_tools_unknown_tool() -> None:
    state = _state_with([_tc("ghost", {}, "c1")])

    cmd = await tools_node(
        state,
        Runtime(context=GraphContext(model=bindable_chat_model([]), tools_by_name={})),
    )

    update = cmd.update
    assert update is not None
    msg = update["stm"][0]
    assert msg.status == "error"
    assert "unknown tool" in msg.content


@pytest.mark.asyncio
async def test_tools_subagent_writes_to_work_history() -> None:
    """Субагент пишет результаты в work_history (не в stm)."""
    state = _state_with([_tc("search", {"q": "x"}, "c1")], processing_mode="subagent")
    ctx = GraphContext(
        model=bindable_chat_model([]),
        tools_by_name={"search": echo_tool("search", "ok")},
    )

    cmd = await tools_node(state, Runtime(context=ctx))

    update = cmd.update
    assert update is not None
    assert "work_history" in update
    assert update["work_history"][0].content == "ok"


def _artifact_ref(
    *, artifact_id: str = "a1xxxx", filename: str = "report.csv"
) -> ArtifactRef:
    return ArtifactRef(
        artifact_id=artifact_id,
        type="document",
        artifact_user_name=filename,
        storage_key=f"u/{artifact_id}/data",
    )


@pytest.mark.asyncio
async def test_tools_collects_created_from_artifact_channel() -> None:
    """Тул вернул артефакт (ToolMessage.artifact) → update['created_artifacts']."""
    ref = _artifact_ref()
    state = _state_with([_tc("gen", {}, "c1")])
    ctx = GraphContext(
        model=bindable_chat_model([]),
        tools_by_name={"gen": artifact_tool("gen", "готово", [ref])},
    )

    cmd = await tools_node(state, Runtime(context=ctx))

    update = cmd.update
    assert update is not None
    assert update["created_artifacts"] == [ref]


@pytest.mark.asyncio
async def test_send_artifact_resolves_to_presented() -> None:
    """send_artifact_to_user([llm_name]) → ref из created в presented + success ToolMessage."""
    ref = _artifact_ref()
    state = _state_with(
        [
            _tc(
                SEND_ARTIFACT_TO_USER_NAME,
                {"artifact_llm_names": [ref.artifact_llm_name]},
                "c1",
            )
        ]
    ).model_copy(update={"created_artifacts": [ref]})
    ctx = GraphContext(model=bindable_chat_model([]))

    cmd = await tools_node(state, Runtime(context=ctx))

    update = cmd.update
    assert update is not None
    assert update["presented_artifacts"] == [ref]
    msg = update["stm"][0]
    assert msg.status != "error"
    assert ref.artifact_llm_name in msg.content


@pytest.mark.asyncio
async def test_send_artifact_unknown_name_errors_without_presented() -> None:
    """Перевранный llm_name → status=error, presented не пишется."""
    ref = _artifact_ref()
    state = _state_with(
        [
            _tc(
                SEND_ARTIFACT_TO_USER_NAME,
                {"artifact_llm_names": ["wrong_zzzzzz.csv"]},
                "c1",
            )
        ]
    ).model_copy(update={"created_artifacts": [ref]})
    ctx = GraphContext(model=bindable_chat_model([]))

    cmd = await tools_node(state, Runtime(context=ctx))

    update = cmd.update
    assert update is not None
    assert "presented_artifacts" not in update
    assert update["stm"][0].status == "error"


@pytest.mark.asyncio
async def test_send_artifact_subagent_writes_no_presented() -> None:
    """Субагент: send_artifact не пишет presented (отдаёт только top-level)."""
    ref = _artifact_ref()
    state = _state_with(
        [
            _tc(
                SEND_ARTIFACT_TO_USER_NAME,
                {"artifact_llm_names": [ref.artifact_llm_name]},
                "c1",
            )
        ],
        processing_mode="subagent",
    ).model_copy(update={"created_artifacts": [ref]})
    ctx = GraphContext(model=bindable_chat_model([]))

    cmd = await tools_node(state, Runtime(context=ctx))

    update = cmd.update
    assert update is not None
    assert "presented_artifacts" not in update


@pytest.mark.asyncio
async def test_delegate_merges_child_created_artifacts() -> None:
    """Субагентский артефакт доезжает родителю: имя в content + ToolMessage.artifact + мердж."""
    ref = _artifact_ref()
    state = _state_with([_tc(DELEGATE_SUBTASK_NAME, {"task": "сделай"}, "c1")])

    async def _ainvoke(payload: Any, **_: Any) -> dict[str, Any]:
        return {"result": "готово", "created_artifacts": [ref]}

    graph = MagicMock()
    graph.ainvoke = _ainvoke
    ctx = GraphContext(graph=graph, model=bindable_chat_model([]))

    cmd = await tools_node(state, Runtime(context=ctx))

    update = cmd.update
    assert update is not None
    msg = update["stm"][0]
    assert ref.artifact_llm_name in msg.content  # parent-visible текст
    assert msg.artifact == [ref]  # структурный канал
    assert update["created_artifacts"] == [ref]  # смерджено в родителя


def _concurrency_tracking_tool(name: str, tracker: dict[str, int]) -> StructuredTool:
    """Тул, считающий пик одновременно активных вызовов (через tracker)."""

    async def _run(**_: Any) -> str:
        tracker["active"] += 1
        tracker["peak"] = max(tracker["peak"], tracker["active"])
        await asyncio.sleep(0.01)
        tracker["active"] -= 1
        return "done"

    return StructuredTool.from_function(
        coroutine=_run,
        name=name,
        description="d",
        args_schema={"type": "object"},
        infer_schema=False,
    )


@pytest.mark.asyncio
async def test_tools_serial_server_runs_sequentially() -> None:
    """Два вызова к serial-серверу (один connection_id) не перекрываются — семафор=1."""
    tracker = {"active": 0, "peak": 0}
    state = _state_with([_tc("srv__a", {}, "c1"), _tc("srv__b", {}, "c2")])
    ctx = GraphContext(
        model=bindable_chat_model([]),
        tools_by_name={
            "srv__a": _concurrency_tracking_tool("srv__a", tracker),
            "srv__b": _concurrency_tracking_tool("srv__b", tracker),
        },
        serial_tool_servers={"srv__a": "cid", "srv__b": "cid"},
    )

    cmd = await tools_node(state, Runtime(context=ctx))

    assert cmd.goto == "react"
    assert tracker["peak"] == 1  # сериализовано: пик активности = 1


@pytest.mark.asyncio
async def test_tools_parallel_server_runs_concurrently() -> None:
    """Без serial_tool_servers вызовы перекрываются (текущее параллельное поведение)."""
    tracker = {"active": 0, "peak": 0}
    state = _state_with([_tc("srv__a", {}, "c1"), _tc("srv__b", {}, "c2")])
    ctx = GraphContext(
        model=bindable_chat_model([]),
        tools_by_name={
            "srv__a": _concurrency_tracking_tool("srv__a", tracker),
            "srv__b": _concurrency_tracking_tool("srv__b", tracker),
        },
    )

    cmd = await tools_node(state, Runtime(context=ctx))

    assert cmd.goto == "react"
    assert tracker["peak"] == 2  # параллельно: оба вызова активны одновременно


# ── Общий пул attached + created и передача файлов субагенту ────────


def _attached_state(
    tool_calls: list[dict[str, Any]], attached: list[ArtifactRef]
) -> OrchestrationState:
    """State как _state_with, но с приложенными юзером файлами в input."""
    lane: list[BaseMessage] = [AIMessage(content="", tool_calls=tool_calls)]
    return OrchestrationState(
        input=InputContext(message="x", request_id="r1", attached_artifacts=attached),
        processing_mode="task",
        stm=lane,
    )


def _hydrator(payloads: dict[str, bytes]):
    """Pre-bound гидрация как в runtime, но с fake-чтением байтов из словаря."""

    async def read_image(artifact_id: str) -> bytes:
        return payloads[artifact_id]

    async def hydrate(messages: list[BaseMessage]) -> list[BaseMessage]:
        return await hydrate_image_artifacts(
            messages,
            read_image=read_image,
            max_history_images=6,
            max_image_bytes=1_000,
        )

    return hydrate


@pytest.mark.asyncio
async def test_send_artifact_resolves_attached_from_input() -> None:
    """Приложенный юзером файл резолвится send_artifact_to_user (общий пул)."""
    ref = _artifact_ref(artifact_id="img-aaa111", filename="photo.jpg")
    state = _attached_state(
        [
            _tc(
                SEND_ARTIFACT_TO_USER_NAME,
                {"artifact_llm_names": [ref.artifact_llm_name]},
                "c1",
            )
        ],
        attached=[ref],
    )

    cmd = await tools_node(
        state, Runtime(context=GraphContext(model=bindable_chat_model([])))
    )

    update = cmd.update
    assert update is not None
    assert update["presented_artifacts"] == [ref]
    assert update["stm"][0].status != "error"


@pytest.mark.asyncio
async def test_delegate_seeds_child_with_attached_images() -> None:
    """delegate(artifact_llm_names) → стартовый Human ребёнка: задача + MD-блок + image-блок."""
    ref = ArtifactRef(
        artifact_id="img-aaa111",
        artifact_user_name="photo.jpg",
        type="image",
        description="кот",
    )
    child_model = capturing_stream_model()
    state = _attached_state(
        [
            _tc(
                DELEGATE_SUBTASK_NAME,
                {"task": "опиши фото", "artifact_llm_names": [ref.artifact_llm_name]},
                "c1",
            )
        ],
        attached=[ref],
    )
    ctx = GraphContext(
        graph=build_graph(),
        model=child_model,
        hydrate_images=_hydrator({"img-aaa111": b"jpg-bytes"}),
    )

    cmd = await tools_node(state, Runtime(context=ctx))

    update = cmd.update
    assert update is not None
    assert update["stm"][0].status != "error"
    seeded = [
        m
        for m in child_model.captured
        if isinstance(m, HumanMessage) and isinstance(m.content, list)
    ]
    assert seeded, "ребёнок не получил гидрированный Human"
    blocks = seeded[0].content
    assert isinstance(blocks, list)
    text_block = blocks[0]
    assert isinstance(text_block, dict)
    assert text_block["type"] == "text"
    assert "опиши фото" in text_block["text"]
    assert "Приложенные файлы:" in text_block["text"]
    assert any(isinstance(b, dict) and b.get("type") == "image" for b in blocks)


@pytest.mark.asyncio
async def test_delegate_seed_without_vision_stays_text() -> None:
    """hydrate_images=None (модель без vision) → сид остаётся текстовым: задача + MD-блок."""
    ref = ArtifactRef(
        artifact_id="img-aaa111", artifact_user_name="photo.jpg", type="image"
    )
    child_model = capturing_stream_model()
    state = _attached_state(
        [
            _tc(
                DELEGATE_SUBTASK_NAME,
                {"task": "опиши фото", "artifact_llm_names": [ref.artifact_llm_name]},
                "c1",
            )
        ],
        attached=[ref],
    )
    ctx = GraphContext(graph=build_graph(), model=child_model)

    await tools_node(state, Runtime(context=ctx))

    seeded = [
        m
        for m in child_model.captured
        if isinstance(m, HumanMessage) and isinstance(m.content, str)
    ]
    assert seeded
    assert "опиши фото" in seeded[0].content
    assert "Приложенные файлы:" in seeded[0].content


@pytest.mark.asyncio
async def test_delegate_reports_missing_artifact_names() -> None:
    """Ненайденные имена не передаются воркеру и явно перечислены в ToolMessage."""
    child_model = capturing_stream_model()
    state = _attached_state(
        [
            _tc(
                DELEGATE_SUBTASK_NAME,
                {"task": "опиши", "artifact_llm_names": ["nope_zzzzzz.jpg"]},
                "c1",
            )
        ],
        attached=[],
    )
    ctx = GraphContext(graph=build_graph(), model=child_model)

    cmd = await tools_node(state, Runtime(context=ctx))

    update = cmd.update
    assert update is not None
    msg = update["stm"][0]
    assert "nope_zzzzzz.jpg" in msg.content
    assert "не найдены" in msg.content
    # Сид не создавался: ребёнок стартовал обычным путём, задача — plain str Human.
    assert all(
        not isinstance(m.content, list)
        for m in child_model.captured
        if isinstance(m, HumanMessage)
    )
