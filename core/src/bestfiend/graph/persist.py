"""Персист хода в STM: срез turn'а из stm → WriteTurnRequest → memory.write.

Формат записи: user_message (ведущий Human) + react_loop (середина без
финального plain-AI) + ai_message (доставленный текст). Fail-soft: ответ
юзеру уже доставлен, сбой персиста логируется и глотается.
"""

from typing import Any

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    message_chunk_to_message,
    messages_to_dict,
)
from loguru import logger
import orjson

from bestfiend.contracts.events import InputEvent
from bestfiend.graph.attached_artifacts import strip_image_blocks
from bestfiend.memory.contracts import WriteTurnRequest
from bestfiend.memory.runtime import MemoryRuntime
from bestfiend.memory.write_pipeline import write as memory_write
from bestfiend.primitives.tokenizer import count_tokens


async def persist_turn(
    event: InputEvent,
    *,
    turn_messages: list[BaseMessage],
    answer_text: str,
    memory_runtime: MemoryRuntime,
) -> None:
    """Сохраняет ход в STM: user_message + react_loop + ai_message (доставленный текст).

    Single-phase, fail-soft. `react_loop` санируется от осиротевшего хвостового
    `AI(tool_calls)` без ответного `ToolMessage` (иначе отравит будущие загрузки).
    """
    try:
        # Снимаем гидрированные image-блоки: в лог уходит только текст —
        # формат записей идентичен догидрационному (без base64).
        turn_messages = strip_image_blocks(turn_messages)
        user_msg, react_loop = _split_turn(turn_messages)
        react_loop = _sanitize_react_loop(react_loop)
        user_dicts = messages_to_dict([user_msg]) if user_msg is not None else []
        loop_dicts = messages_to_dict(react_loop)
        ai_dicts = messages_to_dict([AIMessage(content=answer_text)])
        request = WriteTurnRequest(
            request_id=event.request_id,
            created_at=event.created_at,
            user_message=user_dicts,
            react_loop=loop_dicts,
            ai_message=ai_dicts,
            token_count_full=_count_turn_tokens(user_dicts + loop_dicts + ai_dicts),
            token_count_loop=_count_turn_tokens(loop_dicts),
        )
        await memory_write(event.user_id, request, memory_runtime)
    except Exception as exc:
        logger.warning("graph.persist: turn persist failed: {}", exc)


def _split_turn(
    turn_messages: list[BaseMessage],
) -> tuple[HumanMessage | None, list[BaseMessage]]:
    """Ведущий Human + середина без хвостового plain-AI (это ответ — пересоберём из answer_text)."""
    if not turn_messages:
        return None, []
    head = turn_messages[0]
    user_msg = head if isinstance(head, HumanMessage) else None
    rest = turn_messages[1:] if user_msg is not None else list(turn_messages)
    if rest and isinstance(rest[-1], AIMessage) and not rest[-1].tool_calls:
        rest = rest[:-1]  # финальный ответ — пересобирается из answer_text
    return user_msg, [message_chunk_to_message(m) for m in rest]


def _sanitize_react_loop(loop: list[BaseMessage]) -> list[BaseMessage]:
    """Снимает хвостовой AI(tool_calls) без ответного ToolMessage (отравил бы будущие загрузки)."""
    out = list(loop)
    while out and isinstance(out[-1], AIMessage) and out[-1].tool_calls:
        out.pop()
    return out


def _count_turn_tokens(message_dicts: list[dict[str, Any]]) -> int:
    """Токены сериализованного messages_to_dict-списка (грубая оценка под бюджет окна)."""
    return count_tokens(orjson.dumps(message_dicts).decode())
