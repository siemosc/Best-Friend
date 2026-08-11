"""Контракты memory (in-process)."""

from datetime import datetime
from typing import Any

from langchain_core.messages import BaseMessage
from pydantic import BaseModel, ConfigDict, Field


class MemoryContext(BaseModel):
    """Контекст памяти для графа: хвост лога + рендеренные блоки.

    journal/profile — стабильная часть (system-блок react), recall — волатильная
    (эфемерное обогащение Human). Пустая строка = блок не рендерится.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    log_tail: list[BaseMessage] = Field(default_factory=list)
    journal: str = Field(default="")
    profile: str = Field(default="")
    recall: str = Field(default="")


class WriteTurnRequest(BaseModel):
    """Один ход для записи в лог (messages_to_dict-представление)."""

    request_id: str = Field(description="Request ID хода (= Langfuse session_id).")
    created_at: datetime = Field(description="UTC-момент хода.")
    user_message: list[dict[str, Any]] = Field(
        default_factory=list, description="messages_to_dict([HumanMessage])."
    )
    react_loop: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Внутренний цикл (AI(tool_calls)+ToolMessage); [] если тулов не было.",
    )
    ai_message: list[dict[str, Any]] = Field(
        default_factory=list,
        description="messages_to_dict([AIMessage]) — доставленный на UI ответ.",
    )
    token_count_full: int = Field(default=0, description="Токены всего хода.")
    token_count_loop: int = Field(default=0, description="Токены react_loop.")
