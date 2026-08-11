"""Runtime-context графа: иммутабельные зависимости, подаются при invoke.

Живые объекты (executor, собранные `StructuredTool`'ы) живут здесь, не в state —
state остаётся serializable. Наполняет GraphRuntime и подаёт через
`compiled.ainvoke(input, context=GraphContext(...))`. Нода читает `runtime.context`,
писать в него не может (`Runtime` frozen + `tools_by_name` запечатан в read-only view).
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.tools import StructuredTool

from bestfiend.graph.attached_artifacts import HydrateImages
from bestfiend.graph.config import (
    CHILD_RECURSION_LIMIT_DEFAULT,
    MAX_RECURSION_DEPTH_DEFAULT,
    SOFT_GATE_LIMIT_DEFAULT,
)


@dataclass(frozen=True, kw_only=True)
class GraphContext:
    """Иммутабельные runtime-зависимости графа (read-only в нодах).

    `kw_only` — чтобы обязательная `model` (инвариант рантайма: всегда задана)
    соседствовала с полями-с-дефолтами без ordering-конфликта dataclass.
    """

    # Одна модель на весь граф. GraphRuntime всегда её заполняет — ноды зовут
    # `ctx.model` без Optional-проверок.
    model: BaseChatModel
    tools_by_name: Mapping[str, StructuredTool] = field(default_factory=dict)
    # Имена тулзов, доступных только top-level (память и т.п.): react исключает
    # их из bind для субагентов; исполнение — общее, через tools_by_name.
    top_level_only_tool_names: frozenset[str] = frozenset()
    # namespaced tool → connection_id, только для серверов без параллельности —
    # tools-нода сериализует вызовы к ним (семафор=1 per-server). Остальные/не-MCP
    # тулы здесь отсутствуют → исполняются параллельно.
    serial_tool_servers: Mapping[str, str] = field(default_factory=dict)
    # Скомпилированный граф для delegate_subtask (само-рекурсия); заполняет runtime.
    # Any — pass-through (только `.ainvoke`); точный generic CompiledStateGraph
    # тащит 4 параметра + self-reference на GraphContext.
    graph: Any = None
    # Pre-bound гидрация image-артефактов (artifacts + user_id + лимиты замкнуты
    # в runtime). None = модель без vision — картинки остаются текстовыми рефами.
    # Нужна нодам (delegate seed'ит ленту субагента), поэтому едет в context.
    hydrate_images: HydrateImages | None = None
    # Бюджеты рекурсии: GraphRuntime заполняет из GraphSettings; дефолты — те же
    # константы config, чтобы прямые конструкции в тестах получали рабочие значения.
    soft_gate_limit: int = SOFT_GATE_LIMIT_DEFAULT
    max_recursion_depth: int = MAX_RECURSION_DEPTH_DEFAULT
    child_recursion_limit: int = CHILD_RECURSION_LIMIT_DEFAULT

    def __post_init__(self) -> None:
        """Запечатывает mapping-поля в read-only view — context иммутабелен."""
        object.__setattr__(
            self, "tools_by_name", MappingProxyType(dict(self.tools_by_name))
        )
        object.__setattr__(
            self,
            "serial_tool_servers",
            MappingProxyType(dict(self.serial_tool_servers)),
        )
