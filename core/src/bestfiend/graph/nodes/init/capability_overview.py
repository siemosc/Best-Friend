"""Рендер обзора возможностей для system prompt: MCP tool-серверы и их тулы."""

from bestfiend.graph.state import ToolServerEntryView


_OVERVIEW_HEADER = "# Tool Servers"


def render_capability_overview(catalog: list[ToolServerEntryView]) -> str:
    """Компактный обзор возможностей для системного промпта."""
    if not catalog:
        return ""
    blocks = [_render_overview_block(server) for server in catalog]
    return f"{_OVERVIEW_HEADER}\n\n" + "\n\n".join(blocks)


def _render_overview_block(server: ToolServerEntryView) -> str:
    """Рендерит tool-сервер (instructions целиком + его namespaced-тулы) для overview."""
    lines: list[str] = [f"## Server: `{server.name}`"]

    if server.instructions:
        lines.append(server.instructions.strip())

    for tool in server.tools:
        lines.append(f"- **{tool.name}**: {_flatten_paragraphs(tool.description)}")

    return "\n".join(lines)


def _flatten_paragraphs(text: str) -> str:
    """Схлопывает абзацные разрывы в пробелы."""
    return " ".join(p.strip() for p in text.strip().split("\n\n") if p.strip())
