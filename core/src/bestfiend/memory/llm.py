"""Structured-вызов LLM памяти: один паттерн для всех фоновых читателей лога.

build_chat_model → with_structured_output → ainvoke(llm_run_config) →
model_validate. Fail-soft: любой сбой → None, вызывающий скипает шаг
(watermark не двигается / fail-open ADD / FIFO-страховка — по месту).
"""

from typing import Any, TypeVar
from uuid import UUID

from langchain_core.messages import BaseMessage
from loguru import logger
from pydantic import BaseModel

from bestfiend.ai.llm import build_chat_model
from bestfiend.memory.tracing import llm_run_config


_SchemaT = TypeVar("_SchemaT", bound=BaseModel)


async def invoke_structured(
    llm_config: dict[str, Any],
    schema: type[_SchemaT],
    messages: list[BaseMessage],
    *,
    user_id: UUID,
    task: str,
) -> _SchemaT | None:
    """Structured-вызов LLM; любой сбой → None (шаг скипается, пайплайн живёт).

    Вызывается строго вне транзакций БД (сетевой вызов не держит соединение
    пула). `task` — префикс warning-лога («Observer», «sleep cards», …).
    """
    try:
        model = build_chat_model(llm_config)
        structured = model.with_structured_output(schema)
        result = await structured.ainvoke(messages, config=llm_run_config())
        return schema.model_validate(result)
    except Exception as exc:  # noqa: BLE001 — фоновый пайплайн не валит процесс
        logger.warning("{}: LLM call failed user_id={}: {}", task, user_id, exc)
        return None
