"""Tool send_artifact_to_user: отдаёт выбранные артефакты юзеру файлом.

Routing-only StructuredTool — исполняется спец-веткой в tools-ноде (не через
coroutine), как `delegate_subtask`. Биндится только top-level (субагент юзеру не
отвечает). tools-нода резолвит `artifact_llm_name` по `created_artifacts` и пишет
выбранное в `presented_artifacts`.
"""

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from bestfiend.graph.nodes.react.routing_only_tool import unreachable_tool_callback


SEND_ARTIFACT_TO_USER_NAME = "send_artifact_to_user"


class _SendArtifactArgs(BaseModel):
    """Аргументы send_artifact_to_user."""

    artifact_llm_names: list[str] = Field(
        description=(
            "Names of the artifacts to deliver to the user as files. Use the exact "
            "names shown in the tool results (e.g. report_3f9a2c.csv). Only attach "
            "artifacts the user actually needs."
        ),
    )


SEND_ARTIFACT_TO_USER_TOOL = StructuredTool.from_function(
    coroutine=unreachable_tool_callback(SEND_ARTIFACT_TO_USER_NAME),
    name=SEND_ARTIFACT_TO_USER_NAME,
    description=(
        "Attach one or more files you produced during this turn and send them to "
        "the user. Pass the artifact names exactly as they appear in the tool "
        "results. IMPORTANT: this is the ONLY way to hand a file to the user; "
        "refer to files in your text answer by their plain name."
    ),
    args_schema=_SendArtifactArgs,
)
