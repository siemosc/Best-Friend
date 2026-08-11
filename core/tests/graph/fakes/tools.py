"""Фабрики инструментов для graph-тестов."""

from typing import Any

from langchain_core.tools import StructuredTool

from bestfiend.contracts.artifacts import ArtifactRef


_EMPTY_SCHEMA: dict[str, Any] = {"type": "object", "properties": {}}


def echo_tool(name: str, result: str) -> StructuredTool:
    """Создаёт инструмент с фиксированным результатом."""

    async def _run(**_: Any) -> str:
        return result

    return StructuredTool.from_function(
        coroutine=_run,
        name=name,
        description=name,
        args_schema=_EMPTY_SCHEMA,
    )


def raising_tool(name: str) -> StructuredTool:
    """Создаёт инструмент, который всегда выбрасывает ошибку."""

    async def _run(**_: Any) -> str:
        raise RuntimeError("boom")

    return StructuredTool.from_function(
        coroutine=_run,
        name=name,
        description=name,
        args_schema=_EMPTY_SCHEMA,
    )


def artifact_tool(name: str, result: str, refs: list[ArtifactRef]) -> StructuredTool:
    """Создаёт инструмент, возвращающий текст и ссылки на артефакты."""

    async def _run(**_: Any) -> tuple[str, list[ArtifactRef]]:
        return result, refs

    return StructuredTool.from_function(
        coroutine=_run,
        name=name,
        description=name,
        args_schema=_EMPTY_SCHEMA,
        response_format="content_and_artifact",
    )
