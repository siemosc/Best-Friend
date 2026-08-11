"""Init-нода: рендер промпт-контекста из raw-полей state.

Первая нода графа. Рендерит блоки system-промпта (environment, capability
overview, инструкции, память) из `state.input`. Тулы НЕ собирает — они приходят
готовыми в иммутабельном Runtime context (строит GraphRuntime). md-каталог
тулов не рендерим: тулы видны модели из `bind_tools`.
"""

from langgraph.runtime import Runtime
from langgraph.types import Command
from loguru import logger

from bestfiend.graph.context import GraphContext
from bestfiend.graph.prompts import render_environment
from bestfiend.graph.state import OrchestrationState, RenderedPrompts

from .capability_overview import render_capability_overview
from .user_instruction import render_user_instruction


async def init_node(
    state: OrchestrationState,
    runtime: Runtime[GraphContext],
) -> Command:
    """Рендерит промпт-секции и роутит в react (задача из `input.message`)."""
    ctx = state.input
    uc = ctx.user_environment
    prompts = RenderedPrompts(
        environment=render_environment(
            timezone=(uc.timezone or None) if uc else None,
            city=(uc.city or None) if uc else None,
            country=(uc.country or None) if uc else None,
        ),
        capability_overview=render_capability_overview(ctx.tool_catalog),
        user_instruction=render_user_instruction(ctx.user_instruction),
        memory_stable=_render_memory_stable(ctx.profile, ctx.journal),
        # Recall — волатильный блок (эфемерное обогащение Human), идёт как есть.
        memory_recall=ctx.recall,
    )

    tools = runtime.context.tools_by_name if runtime.context else {}
    logger.debug(
        "init: rendered prompts request_id={} mode={} tools_available={}",
        ctx.request_id,
        state.processing_mode,
        len(tools),
    )

    # Все режимы: react получает задачу из input.message. processing_mode влияет
    # только на выбор промпта в react (work / subagent), не на роутинг init.
    return Command(
        update={"prompts": prompts, "task_for_react": ctx.message},
        goto="react",
    )


def _render_memory_stable(profile: str, journal: str) -> str:
    """Стабильный блок памяти для system: профиль + журнал (порядок — по волатильности)."""
    parts = [part for part in (profile, journal) if part]
    return "\n\n".join(parts)
