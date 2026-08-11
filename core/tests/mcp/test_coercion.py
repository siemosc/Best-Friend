"""coerce_tool_result: контрактный диспетчер CallToolResult → (content, artifacts).

Контракт распознаётся по структуре (result + artifacts), не по имени сервера.
Кривой/частичный контракт деградирует в generic (серверный текст).
"""

from typing import Any

from fastmcp.client.client import CallToolResult
from mcp.types import TextContent

from bestfiend.contracts.artifacts import ArtifactRef
from bestfiend.mcp.coercion import coerce_tool_result


def _result(
    *,
    content: list[Any] | None = None,
    structured: dict[str, Any] | None = None,
    is_error: bool = False,
) -> CallToolResult:
    return CallToolResult(
        content=content or [],
        structured_content=structured,
        meta=None,
        is_error=is_error,
    )


def _text(value: str) -> TextContent:
    return TextContent(type="text", text=value)


def test_generic_text_from_content_blocks() -> None:
    content, artifacts = coerce_tool_result(
        _result(content=[_text("hello"), _text("world")])
    )
    assert content == "hello\nworld"
    assert artifacts is None


def test_generic_falls_back_to_structured_json_when_no_text() -> None:
    content, artifacts = coerce_tool_result(
        _result(content=[], structured={"temp": 22})
    )
    assert "temp" in content
    assert "22" in content
    assert artifacts is None


def test_artifact_contract_renders_and_returns_refs() -> None:
    structured = {
        "result": "Отчёт готов",
        "artifacts": [
            {"artifact_id": "x1y2z3", "artifact_user_name": "report.csv", "type": "csv"}
        ],
    }
    content, artifacts = coerce_tool_result(_result(structured=structured))
    assert "Отчёт готов" in content
    assert "report_x1y2z3.csv" in content  # artifact_llm_name = {stem}_{id[-6:]}{ext}
    assert artifacts is not None
    assert len(artifacts) == 1
    assert isinstance(artifacts[0], ArtifactRef)
    assert artifacts[0].artifact_id == "x1y2z3"


def test_artifact_render_is_bullet_with_description() -> None:
    structured = {
        "result": "Готово",
        "artifacts": [
            {
                "artifact_id": "x1y2z3",
                "artifact_user_name": "report.csv",
                "type": "csv",
                "description": "Сводка за май",
            }
        ],
    }
    content, _ = coerce_tool_result(_result(structured=structured))
    assert "Созданные артефакты:" in content
    assert "- `report_x1y2z3.csv` — Сводка за май" in content


def test_is_error_yields_error_text() -> None:
    content, artifacts = coerce_tool_result(
        _result(content=[_text("boom")], is_error=True)
    )
    assert content.startswith("Ошибка тула:")
    assert "boom" in content
    assert artifacts is None


def test_malformed_artifact_contract_degrades_to_generic() -> None:
    # artifact без обязательного artifact_id → ValidationError → generic путь
    structured = {"result": "x", "artifacts": [{"type": "csv"}]}
    result = _result(content=[_text("server text")], structured=structured)
    content, artifacts = coerce_tool_result(result)
    assert content == "server text"
    assert artifacts is None


def test_structured_without_artifacts_is_generic() -> None:
    # result есть, artifacts нет → не artifact-путь → серверный текст из .content
    content, artifacts = coerce_tool_result(
        _result(content=[_text("plain")], structured={"result": "x"})
    )
    assert content == "plain"
    assert artifacts is None
