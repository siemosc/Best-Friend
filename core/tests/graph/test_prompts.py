"""Тесты сборки промпта react-ноды: стабильный system-блок + волатильный runtime-контекст.

System (render_react_system) кешируется через turn'ы — только rules + user_instruction +
capability_overview. Волатильное (environment, memory) уходит в render_react_runtime для
эфемерного обогащения Human. Артефакты модель видит через ToolMessage.content, не в System.
"""

from bestfiend.graph.nodes.react.prompts import (
    SUBAGENT_RULES,
    WORK_RULES,
    render_react_runtime,
    render_react_system,
)
from bestfiend.graph.state import InputContext, OrchestrationState, RenderedPrompts


def _state(
    *, subagent: bool, prompts: RenderedPrompts | None = None
) -> OrchestrationState:
    return OrchestrationState(
        input=InputContext(message="x", request_id="r1"),
        processing_mode="subagent" if subagent else "task",
        prompts=prompts or RenderedPrompts(),
    )


def test_system_top_level_has_stable_blocks() -> None:
    """Top-level system: rules → user_instruction → caps → memory_stable; без env/recall."""
    prompts = RenderedPrompts(
        environment="ENV",
        memory_stable="STABLE_MEM",
        memory_recall="RECALL_MEM",
        user_instruction="USER_INSTR",
        capability_overview="CAPS",
    )
    system = render_react_system(_state(subagent=False, prompts=prompts))

    assert WORK_RULES in system
    assert "USER_INSTR" in system
    assert "CAPS" in system
    # Стабильная память — в system, в конце блока (журнал — самая волатильная
    # из стабильных частей, хвостовое размещение продлевает кеш префикса).
    assert "STABLE_MEM" in system
    # Волатильное в System не попадает (стабильность кеш-префикса).
    assert "ENV" not in system
    assert "RECALL_MEM" not in system
    # Порядок: rules → --- → user_instruction → capability_overview → memory_stable.
    assert (
        system.index(WORK_RULES)
        < system.index("\n\n---\n\n")
        < system.index("USER_INSTR")
        < system.index("CAPS")
        < system.index("STABLE_MEM")
    )


def test_system_subagent_uses_subagent_rules_and_no_memory() -> None:
    """Subagent system берёт SUBAGENT_RULES; память верхнего уровня не подмешивается."""
    system = render_react_system(
        _state(
            subagent=True,
            prompts=RenderedPrompts(user_instruction="UI", memory_stable="STABLE_MEM"),
        )
    )

    assert SUBAGENT_RULES in system
    assert WORK_RULES not in system
    assert "STABLE_MEM" not in system


def test_system_omits_empty_blocks() -> None:
    """Пустые блоки фильтруются — только непустой rules, без пустых склеек."""
    system = render_react_system(_state(subagent=False))

    assert system == WORK_RULES
    assert "\n\n\n" not in system


def test_system_has_no_artifact_block() -> None:
    """Артефактный блок ушёл из System навсегда (регресс-сторож)."""
    system = render_react_system(
        _state(subagent=False, prompts=RenderedPrompts(capability_overview="CAPS"))
    )

    assert "Files you can send" not in system


def test_runtime_top_level_is_environment_and_recall() -> None:
    """render_react_runtime top-level: environment + recall внутри <system-reminder>."""
    prompts = RenderedPrompts(environment="ENV", memory_recall="MEM")
    runtime = render_react_runtime(_state(subagent=False, prompts=prompts))

    assert runtime == "<system-reminder>\nENV\n\nMEM\n</system-reminder>"


def test_runtime_skips_empty_blocks() -> None:
    """Пустой блок пропускается; пустые prompts → пустая строка."""
    only_env = render_react_runtime(
        _state(subagent=False, prompts=RenderedPrompts(environment="ENV"))
    )
    only_mem = render_react_runtime(
        _state(subagent=False, prompts=RenderedPrompts(memory_recall="MEM"))
    )
    empty = render_react_runtime(_state(subagent=False))

    assert only_env == "<system-reminder>\nENV\n</system-reminder>"
    assert only_mem == "<system-reminder>\nMEM\n</system-reminder>"
    assert empty == ""


def test_runtime_empty_for_subagent() -> None:
    """Subagent не получает runtime-контекст даже при заданных env/memory."""
    prompts = RenderedPrompts(environment="ENV", memory_recall="MEM")
    runtime = render_react_runtime(_state(subagent=True, prompts=prompts))

    assert runtime == ""
