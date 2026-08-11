"""Общие хелперы сообщений для graph-нод: текст сообщения/чанка + транскрипт для финала.

Используют react / summarize / error — чтобы не дублировать логику.
"""

from langchain_core.messages import AIMessage, BaseMessage, SystemMessage


def message_text(message: BaseMessage) -> str:
    """Текст из сообщения или чанка стрима (str или склейка text-блоков)."""
    content = message.content
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text", "")))
    return "".join(parts)


def history_for_answer(work_history: list[BaseMessage]) -> list[BaseMessage]:
    """Транскрипт без system-сообщений и без незакрытого хвостового tool-call.

    Все `SystemMessage` выкидываем (нода ставит свой system); хвостовой
    `AIMessage` с `tool_calls` — незакрытый вызов (tools его не исполняла) —
    отрезаем, иначе незакрытый tool-протокол сломает API.
    """
    messages = [m for m in work_history if not isinstance(m, SystemMessage)]
    if messages:
        last = messages[-1]
        if isinstance(last, AIMessage) and last.tool_calls:
            return messages[:-1]
    return messages
