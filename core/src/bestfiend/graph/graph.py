"""Сборка графа оркестрации (LangGraph StateGraph).

Топология: `init → react ⇄ tools`, `error` — сток. На сбое любой регулярной
ноды (после узких ретраев) `error_handler=_ON_ERROR` классифицирует и роутит в
error-ноду. error-нода — **без** `error_handler` (иначе рекурсия); её краш ловит
outer net (GraphRuntime). Саб-агенты — рекурсия того же графа через
`delegate_subtask` (исполняет tools-нода). `recursion_limit` — при invoke.
"""

from typing import Any

from langgraph.graph import END, START, StateGraph

from bestfiend.graph.context import GraphContext
from bestfiend.graph.errors import RETRY_POLICY, to_error
from bestfiend.graph.nodes.error import error_node
from bestfiend.graph.nodes.init import init_node
from bestfiend.graph.nodes.react import react_node
from bestfiend.graph.nodes.tools import tools_node
from bestfiend.graph.state import OrchestrationState


# langgraph типизирует `error_handler` как обычный StateNode и не выражает форму
# `(state, error: NodeError)` — рантайм инжектит NodeError по аннотации. Гасим тип.
_ON_ERROR: Any = to_error


def build_graph():
    """Компилирует граф: init → react ⇄ tools; error — сток."""
    workflow = StateGraph(OrchestrationState, context_schema=GraphContext)
    # error_handler=_ON_ERROR на регулярных нодах: сбой → classify → goto error.
    # retry_policy (узкий, транзиентный) — на LLM-ноде react. error-нода — БЕЗ handler'а.
    workflow.add_node(
        "init", init_node, destinations=("react",), error_handler=_ON_ERROR
    )
    workflow.add_node(
        "react",
        react_node,
        destinations=("tools", "error", END),
        error_handler=_ON_ERROR,
        retry_policy=RETRY_POLICY,
    )
    workflow.add_node(
        "tools",
        tools_node,
        destinations=("react",),
        error_handler=_ON_ERROR,
    )
    workflow.add_node("error", error_node, destinations=(END,))  # без error_handler
    workflow.add_edge(START, "init")
    return workflow.compile()
