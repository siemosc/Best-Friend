"""Тесты init-ноды: рендер промптов + унифицированный роутинг в react.

init вызываем напрямую (не через граф): возвращает `Command` → всегда react
(+ task_for_react из input.message), для всех processing_mode.
"""

from langgraph.runtime import Runtime
import pytest

from bestfiend.contracts.user_environment import UserEnvironment
from bestfiend.graph.context import GraphContext
from bestfiend.graph.nodes.init import init_node
from bestfiend.graph.state import (
    InputContext,
    OrchestrationState,
    ToolEntryView,
    ToolServerEntryView,
)
from tests.graph.fakes import bindable_chat_model


def _catalog() -> list[ToolServerEntryView]:
    return [
        ToolServerEntryView(
            name="web",
            instructions="Search the web for info.",
            tools=(
                ToolEntryView(
                    name="search", description="Web search", input_schema=None
                ),
            ),
        )
    ]


@pytest.mark.asyncio
async def test_init_task_mode_routes_to_react() -> None:
    """task: init рендерит секции, кладёт message в task_for_react, goto react."""
    state = OrchestrationState(
        input=InputContext(
            message="найди погоду в Москве",
            request_id="r1",
            user_environment=UserEnvironment(
                timezone="Europe/Moscow", city="Moscow", country="RU"
            ),
            user_instruction="be precise and brief",
            journal="## Журнал наблюдений\n\n[2026-06-09] запись",
            profile="## Профиль пользователя\n\nфакт о юзере",
            recall="## Из памяти\n\nнайденное",
            tool_catalog=_catalog(),
        ),
        processing_mode="task",
    )

    cmd = await init_node(
        state, Runtime(context=GraphContext(model=bindable_chat_model([])))
    )

    assert cmd.goto == "react"
    update = cmd.update
    assert update is not None
    prompts = update["prompts"]
    assert "Europe/Moscow" in prompts.environment
    assert "search" in prompts.capability_overview
    assert "be precise and brief" in prompts.user_instruction
    assert "запись" in prompts.memory_stable
    assert "факт о юзере" in prompts.memory_stable
    # Порядок стабильного блока: профиль раньше журнала (журнал волатильнее).
    assert prompts.memory_stable.index("Профиль") < prompts.memory_stable.index(
        "Журнал"
    )
    assert "найденное" in prompts.memory_recall
    assert update["task_for_react"] == "найди погоду в Москве"


@pytest.mark.asyncio
async def test_init_handles_missing_user_environment() -> None:
    """user_environment=None → environment с Unknown, без падения ZoneInfo('')."""
    state = OrchestrationState(input=InputContext(message="привет", request_id="r3"))

    cmd = await init_node(
        state, Runtime(context=GraphContext(model=bindable_chat_model([])))
    )

    update = cmd.update
    assert update is not None
    assert "Unknown" in update["prompts"].environment
