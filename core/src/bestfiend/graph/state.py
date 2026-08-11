"""Контракты состояния графа (State Schema) для LangGraph.

OrchestrationState — Pydantic v2 state одного запроса под ReAct-топологию:
нативные сообщения LangChain (`BaseMessage` + `add_messages`) и нативный бюджет
шагов (`remaining_steps`). Рекурсивный react с делегированием субагентам.
"""

from dataclasses import dataclass
from typing import Annotated, Any, Literal

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages
from langgraph.managed import RemainingSteps
from pydantic import BaseModel, ConfigDict, Field

from bestfiend.contracts.artifacts import ArtifactRef
from bestfiend.contracts.user_environment import UserEnvironment


def merge_artifacts(
    left: list[ArtifactRef] | None,
    right: list[ArtifactRef] | None,
) -> list[ArtifactRef]:
    """Reducer-аккумулятор артефактов: склейка left+right, дедуп по artifact_id (first-wins)."""
    merged: list[ArtifactRef] = []
    seen: set[str] = set()
    for ref in [*(left or []), *(right or [])]:
        if ref.artifact_id in seen:
            continue
        seen.add(ref.artifact_id)
        merged.append(ref)
    return merged


class ErrorSignal(BaseModel):
    """Сигнал инцидента в ходе обработки запроса.

    Пишут ноды (через error_handler-фабрику), soft-gate на `remaining_steps` и
    outer net рантайма. Единожды за turn; читает error-нода для выбора реакции
    (static / finalize).
    """

    kind: Literal[
        "provider_down",
        "context_exceeded",
        "loop_exhausted",
        "unexpected",
    ] = Field(
        description=(
            "Категория инцидента. `provider_down` — сеть/5xx/429 после ретраев, "
            "401/404 (→ static). `context_exceeded` — превышение контекста "
            "провайдера (→ finalize). `loop_exhausted` — soft-gate "
            "`remaining_steps<=3` (→ finalize). `unexpected` — catch-all любой "
            "код-сбой (→ static)."
        ),
    )
    node: Literal["init", "react", "tools", "error", "graph"] | None = Field(
        default=None,
        description=(
            "Нода-источник инцидента. `graph` — перехват outer net'ом вокруг "
            "графа. `None` зарезервировано; штатный путь всегда называет источник."
        ),
    )
    message: str = Field(
        description=(
            "Техническое описание для трейсинга и логов. НЕ показывается "
            "пользователю — финальный текст формирует error-нода."
        ),
    )


@dataclass(frozen=True, slots=True)
class ToolEntryView:
    """Один tool агента в каталоге."""

    name: str
    description: str
    input_schema: dict[str, Any] | None


@dataclass(frozen=True, slots=True)
class ToolServerEntryView:
    """Один MCP tool-сервер в каталоге для LLM-промптов."""

    name: str
    instructions: str | None
    tools: tuple[ToolEntryView, ...]


class InputContext(BaseModel):
    """Immutable-контекст запроса. Собирается pipeline'ом до старта графа."""

    model_config = ConfigDict(frozen=True)

    message: str = Field(
        description="Текст запроса/события.",
    )
    request_id: str = Field(
        description="Уникальный id запроса для логов и span'ов.",
    )
    attached_artifacts: list[ArtifactRef] = Field(
        default_factory=list,
        description=(
            "Артефакты, пришедшие с запросом (источник — "
            "InputEvent.attached_artifacts). Immutable весь turn."
        ),
    )
    user_environment: UserEnvironment | None = Field(
        default=None,
        description="Окружение пользователя (timezone, city, country).",
    )
    user_instruction: str = Field(
        default="",
        description="User-инструкция (единая на весь граф). Пересмотр при нодах.",
    )
    journal: str = Field(
        default="",
        description=(
            "Журнал наблюдений: датированные заметки Observer'а (in_journal). "
            "Рендерится в стабильный system-блок react."
        ),
    )
    profile: str = Field(
        default="",
        description=(
            "Профиль пользователя: pinned-заметки по секциям. "
            "Рендерится в стабильный system-блок react."
        ),
    )
    recall: str = Field(
        default="",
        description=(
            "Recall-блок: найденное в архиве по текущему сообщению. Волатильный — "
            "эфемерно обогащает Human текущего turn'а. Пустая строка = gate не прошёл."
        ),
    )
    tool_catalog: list[ToolServerEntryView] = Field(
        default_factory=list,
        description="Каталог MCP tool-серверов для рендера промптов.",
    )


class RenderedPrompts(BaseModel):
    """Отрендеренные промпт-блоки для system prompt. Заполняет init-нода.

    Состав пересобирается под новые ноды; пока — текущий набор.
    """

    environment: str = Field(default="", description="Блок окружения.")
    capability_overview: str = Field(
        default="", description="Компактный обзор возможностей."
    )
    user_instruction: str = Field(
        default="", description="User-инструкция пользователя."
    )
    memory_stable: str = Field(
        default="",
        description="Стабильная память (профиль + журнал) — system-блок react.",
    )
    memory_recall: str = Field(
        default="",
        description="Волатильная память (recall-блок) — эфемерное обогащение Human.",
    )


class OrchestrationState(BaseModel):
    """LangGraph State Schema (scope: один запрос).

    ReAct-топология: init → react ⇄ tools, error — сток. react владеет полным
    циклом (plain text = ответ); саб-агенты — рекурсия того же графа через
    delegate_subtask. Serializable: живые `StructuredTool`'ы — в Runtime context.
    """

    # ── Immutable: pipeline собирает до старта графа ─────────────────

    input: InputContext = Field(
        description="Контекст запроса. Неизменяем в ходе обработки.",
    )
    stm: Annotated[list[BaseMessage], add_messages] = Field(
        default_factory=list,
        description=(
            "Рабочая лента top-level (dialog/task): история диалога из memory + "
            "текущий turn. На старте последний элемент — Human текущего запроса "
            "(маркер `turn_start_index`). react/tools/error для top-level пишут "
            "сюда AI/Tool через `add_messages`. memory отдаёт ленту уже как "
            "`BaseMessage` (messages_from_dict)."
        ),
    )
    turn_start_index: int = Field(
        default=0,
        description=(
            "Индекс Human текущего запроса в `stm` на старте хода (ставит runtime: "
            "`len(stm)-1`). Граница turn'а: persist и error-finalize берут срез "
            "`stm[turn_start_index:]` — сообщения текущего хода."
        ),
    )
    processing_mode: Literal["task", "subagent"] = Field(
        default="task",
        description=(
            "Кто инициировал прогон react. `task` — top-level (хинт ingress). "
            "`subagent` — делегированный дочерний прогон (ставит "
            "delegate_subtask). Влияет на выбор промпта react и поведение "
            "soft-gate (subagent → summarize-and-return, top-level → error)."
        ),
    )

    # ── Computed: init рендерит один раз ─────────────────────────────

    prompts: RenderedPrompts = Field(
        default_factory=RenderedPrompts,
        description="Отрендеренные промпт-блоки. Заполняет init-нода.",
    )

    # ── React: задача и история loop'а ───────────────────────────────

    task_for_react: str = Field(
        default="",
        description=(
            "Самодостаточная задача для react. init кладёт `input.message` для "
            "всех режимов; для `subagent` это под-задача из delegate_subtask "
            "(message дочернего payload'а). react всегда читает одну переменную."
        ),
    )
    recursion_depth: int = Field(
        default=0,
        description=(
            "Глубина само-рекурсии react. delegate_subtask повышает на 1 для "
            "дочернего прогона. На пределе (`GraphSettings.max_recursion_depth`) "
            "react не биндит delegate_subtask — дальше делегировать нельзя."
        ),
    )
    work_history: Annotated[list[BaseMessage], add_messages] = Field(
        default_factory=list,
        description=(
            "Рабочая лента субагента (`processing_mode == 'subagent'`): "
            "HumanMessage(`task_for_react`) + нативный loop (AIMessage+ToolMessage). "
            "Top-level пишет в `stm`. System не хранится — prepend на invoke. "
            "Живёт один прогон субагента."
        ),
    )

    # ── Artifacts: аккумуляторы за turn ──────────────────────────────

    created_artifacts: Annotated[list[ArtifactRef], merge_artifacts] = Field(
        default_factory=list,
        description=(
            "Артефакты, созданные работниками за turn (склейка+дедуп по artifact_id). "
            "Наполняет tools_node из ToolMessage.artifact; через границу субагента — "
            "мердж out['created_artifacts'] в run_delegated_subtask. Машинный реестр для "
            "resolve send_artifact_to_user и persist. Модели НЕ рендерится — имена "
            "созданных артефактов модель видит в ToolMessage.content (coercion + "
            "tools_node вклеивают artifact_llm_name)."
        ),
    )
    presented_artifacts: Annotated[list[ArtifactRef], merge_artifacts] = Field(
        default_factory=list,
        description=(
            "Артефакты, отобранные оркестратором к отдаче (send_artifact_to_user). "
            "runtime кладёт в AnswerFinal.attachments → бот шлёт файлом."
        ),
    )

    # ── Output / error ───────────────────────────────────────────────

    result: str = Field(
        default="",
        description=(
            "Терминальный вывод уровня react: финальный ответ (plain text), "
            "summarize-итог (subagent на soft-gate) или текст ошибки. Для "
            "top-level читает runtime; для subagent — tools-нода → ToolMessage."
        ),
    )
    error_signal: ErrorSignal | None = Field(
        default=None,
        description=(
            "Сигнал инцидента. Пишут ноды / soft-gate / outer net; читает error-нода."
        ),
    )

    # ── Infra: managed + метрики ─────────────────────────────────────

    remaining_steps: RemainingSteps = Field(
        default=25,
        description=(
            "LangGraph managed — оставшиеся graph-step'ы до `recursion_limit`. "
            "Основа soft-gate (graceful финал на `soft_gate_limit`). Реальный "
            "бюджет задаёт `GraphSettings.graph_recursion_limit` при invoke."
        ),
    )

    @property
    def is_subagent(self) -> bool:
        """True для делегированного дочернего прогона (`processing_mode == 'subagent'`)."""
        return self.processing_mode == "subagent"

    @property
    def active_history(self) -> list[BaseMessage]:
        """Активная рабочая лента хода: `work_history` для субагента, иначе `stm`."""
        return self.work_history if self.is_subagent else self.stm

    @property
    def active_history_field(self) -> Literal["stm", "work_history"]:
        """Имя поля активной ленты для `Command(update=...)`."""
        return "work_history" if self.is_subagent else "stm"

    @property
    def turn_history(self) -> list[BaseMessage]:
        """Сообщения текущего turn'а: субагент → весь `work_history`; top-level → `stm` от маркера."""
        if self.is_subagent:
            return self.work_history
        return self.stm[self.turn_start_index :]
