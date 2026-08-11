"""Рендер пользовательской инструкции для system prompt."""


def render_user_instruction(instruction: str) -> str:
    """Оборачивает инструкцию в блок для system prompt. Пусто → пустая строка."""
    if not instruction or not instruction.strip():
        return ""
    return f"# User Instructions\n\n{instruction}"
