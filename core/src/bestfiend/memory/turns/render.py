"""Рендер хода лога для LLM-читателей и web-фасада.

Один компактный формат на всех потребителей: промпт Observer, тулза
memory_read_log («вспомнить сцену дословно») и лента лога в web — везде
пользователь и модель видят одну и ту же свёртку хода.
"""

from typing import Any, Final

from bestfiend.memory.turns.contracts import Turn


# Обрезка содержимого tool-результатов в рендере хода: читатель фиксирует
# «что сделали и чем кончилось», трасса инструментов ему не нужна целиком.
_TOOL_RESULT_MAX_CHARS: Final[int] = 300
_TOOL_ARGS_MAX_CHARS: Final[int] = 120


def render_turn_for_reader(turn: Turn) -> str:
    """Один ход для LLM-читателя: user-текст + свёрнутый tool-цикл + ответ."""
    stamp = turn.created_at.strftime("%Y-%m-%d %H:%M")
    lines: list[str] = []
    for raw in turn.user_message:
        lines.append(f"[{stamp}] Пользователь: {_dict_message_text(raw)}")
    lines.extend(_render_loop(turn.react_loop))
    for raw in turn.ai_message:
        lines.append(f"[{stamp}] Ассистент: {_dict_message_text(raw)}")
    return "\n".join(lines)


def _render_loop(react_loop: list[dict[str, Any]]) -> list[str]:
    """Свёртка внутреннего цикла: имена tool-вызовов + обрезанные результаты."""
    lines: list[str] = []
    for raw in react_loop:
        kind = raw.get("type", "")
        data = raw.get("data", {})
        if kind == "ai":
            for call in data.get("tool_calls", []) or []:
                args = _clip(str(call.get("args", {})), _TOOL_ARGS_MAX_CHARS)
                lines.append(f"  [инструмент] {call.get('name', '?')}({args})")
        elif kind == "tool":
            content = _clip(_content_text(data.get("content")), _TOOL_RESULT_MAX_CHARS)
            lines.append(f"  [результат] {content}")
    return lines


def _dict_message_text(raw: dict[str, Any]) -> str:
    """Текст из messages_to_dict-представления сообщения."""
    return _content_text(raw.get("data", {}).get("content"))


def _content_text(content: Any) -> str:
    """content сообщения → текст (multimodal-блоки: только text-части)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts = [
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        return "\n".join(t for t in texts if t)
    return str(content or "")


def _clip(text: str, limit: int) -> str:
    """Обрезает текст до limit с маркером усечения."""
    if len(text) <= limit:
        return text
    return text[:limit] + "…"
