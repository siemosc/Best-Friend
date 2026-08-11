"""Тесты OrchestrationState: дефолты, валидация ErrorSignal, langgraph-совместимость."""

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import END, START, StateGraph
from pydantic import ValidationError
import pytest

from bestfiend.contracts.artifacts import ArtifactRef
from bestfiend.graph.state import (
    ErrorSignal,
    InputContext,
    OrchestrationState,
    RenderedPrompts,
    merge_artifacts,
)


def _input() -> InputContext:
    """Минимальный валидный InputContext для конструирования state."""
    return InputContext(message="привет", request_id="req-1")


def test_minimal_defaults() -> None:
    """OrchestrationState строится из одного input; остальные поля — дефолты."""
    state = OrchestrationState(input=_input())

    assert state.stm == []
    assert state.turn_start_index == 0
    assert state.processing_mode == "task"
    assert state.prompts == RenderedPrompts()
    assert state.task_for_react == ""
    assert state.recursion_depth == 0
    assert state.work_history == []
    assert state.result == ""
    assert state.error_signal is None
    assert state.remaining_steps == 25
    # top-level (task) по умолчанию: активная лента — stm.
    assert state.is_subagent is False
    assert state.active_history_field == "stm"


def test_error_signal_validation() -> None:
    """ErrorSignal принимает новые kind/node и отвергает снесённые литералы."""
    signal = ErrorSignal(kind="provider_down", node="react", message="upstream 503")
    assert signal.kind == "provider_down"
    assert signal.node == "react"

    with pytest.raises(ValidationError):
        ErrorSignal(kind="llm_task_too_hard", message="старый литерал")  # type: ignore[arg-type]

    with pytest.raises(ValidationError):
        ErrorSignal(kind="unexpected", node="planner", message="снесённая нода")  # type: ignore[arg-type]


def test_state_langgraph_roundtrip() -> None:
    """State совместим с langgraph: managed виден в ноде, reducer копит, managed не в output."""

    def work(state: OrchestrationState) -> dict[str, object]:
        # remaining_steps — managed: заполняется рантаймом на исполнении ноды.
        assert isinstance(state.remaining_steps, int)
        return {"result": "done", "work_history": [AIMessage(content="a")]}

    builder = StateGraph(OrchestrationState)
    builder.add_node("work", work)
    builder.add_edge(START, "work")
    builder.add_edge("work", END)
    compiled = builder.compile()

    # langgraph типизирует invoke под полный state, но dict частичного входа — рабочая форма.
    out = compiled.invoke({"input": _input()})  # type: ignore[arg-type]

    assert isinstance(out, dict)
    assert out["result"] == "done"
    assert len(out["work_history"]) == 1
    assert isinstance(out["work_history"][0], AIMessage)
    # Managed-значения исключены из output_channels — наружу не отдаются.
    assert "remaining_steps" not in out


def test_active_history_selects_lane_by_mode() -> None:
    """active_history/field/turn_history переключаются по processing_mode."""
    user = HumanMessage(content="привет")
    ai = AIMessage(content="ответ")

    top = OrchestrationState(
        input=_input(), stm=[user, ai], turn_start_index=0, processing_mode="task"
    )
    assert top.is_subagent is False
    assert top.active_history_field == "stm"
    assert top.active_history == [user, ai]
    assert top.turn_history == [user, ai]  # срез stm от маркера 0

    mid = OrchestrationState(
        input=_input(), stm=[user, ai], turn_start_index=1, processing_mode="task"
    )
    assert mid.turn_history == [ai]  # срез stm[1:] — только текущий turn

    sub = OrchestrationState(
        input=_input(), work_history=[user, ai], processing_mode="subagent"
    )
    assert sub.is_subagent is True
    assert sub.active_history_field == "work_history"
    assert sub.active_history == [user, ai]
    assert sub.turn_history == [user, ai]  # субагент → весь work_history


def test_artifact_accumulators_default_empty() -> None:
    """created_artifacts/presented_artifacts по умолчанию пустые."""
    state = OrchestrationState(input=_input())
    assert state.created_artifacts == []
    assert state.presented_artifacts == []


def test_merge_artifacts_appends_and_dedups() -> None:
    """merge_artifacts: склейка left+right, дедуп по artifact_id (first-wins, порядок)."""
    a = ArtifactRef(
        artifact_id="a", type="document", artifact_user_name="x.md", storage_key="k"
    )
    b = ArtifactRef(
        artifact_id="b", type="document", artifact_user_name="y.md", storage_key="k"
    )
    a_dup = ArtifactRef(
        artifact_id="a", type="image", artifact_user_name="z.png", storage_key="k"
    )

    assert merge_artifacts([a], [b]) == [a, b]
    assert merge_artifacts(None, [a]) == [a]
    assert merge_artifacts([a], None) == [a]

    merged = merge_artifacts([a], [a_dup, b])
    assert [ref.artifact_id for ref in merged] == ["a", "b"]  # дедуп по id
    assert merged[0] is a  # first-wins
