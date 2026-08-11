"""Тулзы памяти для графа: search / save / revise / read_log / track / stats.

Фабрика per-request StructuredTool: выполнение живёт в методах
`MemoryToolHandlers` (tool_handlers.py) с привязкой к (runtime, user_id) —
исполняются стандартной tools-нодой как обычные coroutine-тулзы, без спец-веток.
Биндятся только top-level (фильтр по top_level_only_tool_names в react).
Записи заметок транзакционны и оставляют след в ops-логе (pipeline='tool');
измерения (track) — append-only ряд без ops-лога: MemoryOperation привязан к note_id.
"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from bestfiend.memory.agent_tools.handlers import MemoryToolHandlers
from bestfiend.memory.measurements.contracts import MeasurementBucket
from bestfiend.memory.runtime import MemoryRuntime


MEMORY_SEARCH_NAME = "memory_search"
MEMORY_SAVE_NAME = "memory_save"
MEMORY_REVISE_NAME = "memory_revise"
MEMORY_READ_LOG_NAME = "memory_read_log"
MEMORY_TRACK_NAME = "memory_track"
MEMORY_STATS_NAME = "memory_stats"
MEMORY_TOOL_NAMES: frozenset[str] = frozenset(
    {
        MEMORY_SEARCH_NAME,
        MEMORY_SAVE_NAME,
        MEMORY_REVISE_NAME,
        MEMORY_READ_LOG_NAME,
        MEMORY_TRACK_NAME,
        MEMORY_STATS_NAME,
    }
)

_SEARCH_LIMIT_MAX = 20


class _MemorySearchArgs(BaseModel):
    """Аргументы memory_search."""

    query: str = Field(
        description=(
            "What to look for in long-term memory. Phrase it close to how it could "
            "have been said in past conversations; names and specific terms work best."
        ),
    )
    kinds: (
        list[
            Literal[
                "observation",
                "fact",
                "preference",
                "rule",
                "reflection",
                "entity_card",
                "period_summary",
            ]
        ]
        | None
    ) = Field(
        default=None,
        description=(
            "Optional filter by note kind: observation — dated events from past "
            "conversations; fact — durable facts; preference — user preferences; "
            "rule — working rules; reflection — consolidated journal entries; "
            "entity_card — dossiers about a person/project/topic; period_summary — "
            "weekly digests. Omit to search across all kinds."
        ),
    )
    subjects: list[Literal["user", "agent", "world"]] | None = Field(
        default=None,
        description=(
            "Optional filter by note subject: user — about the user and their "
            "life; agent — about the assistant's own behaviour and working "
            "rules; world — about the outside world, not tied to the user "
            "personally. Omit to search across all subjects."
        ),
    )
    limit: int | None = Field(
        default=None,
        ge=1,
        le=_SEARCH_LIMIT_MAX,
        description="Maximum number of notes to return (default 8).",
    )


class _MemorySaveArgs(BaseModel):
    """Аргументы memory_save."""

    content: str = Field(
        description=(
            "Self-contained statement to remember, with concrete names and values. "
            "Write it in the language of the conversation."
        ),
    )
    kind: Literal["fact", "preference", "rule"] = Field(
        description=(
            "fact — durable fact about the user or their world; preference — how "
            "the user likes things done; rule — a working rule for the assistant."
        ),
    )
    subject: Literal["user", "agent", "world"] = Field(
        description=(
            "Who the statement is about: user — the user and their life; agent — "
            "the assistant's own behaviour and working rules; world — the outside "
            "world, not tied to the user personally."
        ),
    )
    pin: bool = Field(
        default=False,
        description=(
            "true — keep in the always-visible profile (identity, durable "
            "preferences, standing rules). false — store in the searchable archive."
        ),
    )
    pin_section: Literal["identity", "preferences", "relationships", "rules"] | None = (
        Field(
            default=None,
            description="Profile section for pinned notes; required when pin is true.",
        )
    )


class _MemoryReadLogArgs(BaseModel):
    """Аргументы memory_read_log."""

    from_turn: int = Field(
        ge=1,
        description=(
            "First turn number of the range. Memory notes returned by "
            "memory_search carry their source range as «(ходы X–Y)»."
        ),
    )
    to_turn: int = Field(
        ge=1,
        description="Last turn number of the range (inclusive).",
    )


class _MemoryTrackArgs(BaseModel):
    """Аргументы memory_track."""

    metric: str = Field(
        description=(
            "Short reusable metric name in snake_case. Reuse the user's existing "
            "metric names and their language — call memory_stats without "
            "arguments to list them.\n"
            "<good-example>\nweight\n</good-example>\n"
            "<bad-example>\nweight_morning_july_15\n</bad-example>"
        ),
    )
    value: float | None = Field(
        default=None,
        description=(
            "Numeric value when the metric has one (weight, hours, distance). "
            "Omit for pure events like a gym visit or a meal — the record "
            "itself counts as one occurrence."
        ),
    )
    unit: str | None = Field(
        default=None,
        description="Unit of the value, e.g. 'kg', 'h', 'km'. Omit for events.",
    )
    event_time: datetime | None = Field(
        default=None,
        description=(
            "When it actually happened, if the user names a time other than "
            "now (e.g. 'this morning', 'yesterday'). Omit for 'just now'."
        ),
    )
    tags: dict[str, str] | None = Field(
        default=None,
        description=(
            'Optional flat context tags, e.g. {"note": "chest day"} or '
            '{"meal": "lunch"}.'
        ),
    )


class _MemoryStatsArgs(BaseModel):
    """Аргументы memory_stats."""

    metric: str | None = Field(
        default=None,
        description=(
            "Metric to aggregate. Omit to get an overview of all tracked "
            "metrics with their record counts and last values."
        ),
    )
    from_date: datetime | None = Field(
        default=None,
        description="Start of the period (inclusive). Omit for all time.",
    )
    to_date: datetime | None = Field(
        default=None,
        description="End of the period (inclusive). Omit for 'up to now'.",
    )
    group_by: MeasurementBucket | None = Field(
        default=None,
        description=(
            "Bucket the statistics by 'day', 'week' or 'month' to see the "
            "dynamics over time. Omit for totals over the whole period."
        ),
    )


class _MemoryReviseArgs(BaseModel):
    """Аргументы memory_revise."""

    statement_to_replace: str = Field(
        description=(
            "The remembered statement that turned out wrong or outdated, phrased "
            "close to how it was likely stored."
        ),
    )
    corrected_statement: str = Field(
        description=(
            "The corrected statement to remember instead. Self-contained, with "
            "concrete names and values, in the language of the conversation."
        ),
    )
    kind: Literal["fact", "preference", "rule"] = Field(
        default="fact",
        description=(
            "Kind for the corrected statement when no stored note matches. When a "
            "match is found, the original note's kind is kept automatically."
        ),
    )
    subject: Literal["user", "agent", "world"] | None = Field(
        default=None,
        description=(
            "Who the corrected statement is about (user — the user and their "
            "life; agent — the assistant's own behaviour; world — the outside "
            "world), used when no stored note matches. When a match is found, "
            "the original note's subject is kept automatically."
        ),
    )


def build_memory_tools(
    runtime: MemoryRuntime, user_id: UUID
) -> dict[str, StructuredTool]:
    """Собирает тулзы памяти, замкнутые на пользователя текущего запроса."""
    handlers = MemoryToolHandlers(runtime, user_id)

    search_tool = StructuredTool.from_function(
        coroutine=handlers.search,
        name=MEMORY_SEARCH_NAME,
        description=(
            "Search the assistant's long-term memory: past conversations distilled "
            "into dated notes, durable facts and preferences. Use it when the user "
            "asks to recall something, refers to past discussions, or when the "
            "context lacks information you are expected to remember. Returns dated "
            "notes; an empty result after a couple of rephrased attempts means the "
            "memory genuinely has nothing on the topic."
        ),
        args_schema=_MemorySearchArgs,
    )
    save_tool = StructuredTool.from_function(
        coroutine=handlers.save,
        name=MEMORY_SAVE_NAME,
        description=(
            "Save a statement to long-term memory immediately. Use it when the user "
            "explicitly asks to remember something, or states a durable fact, "
            "preference or working rule worth keeping. DO NOT use it for one-off "
            "events or numeric measurements — memory_track handles those."
        ),
        args_schema=_MemorySaveArgs,
    )
    revise_tool = StructuredTool.from_function(
        coroutine=handlers.revise,
        name=MEMORY_REVISE_NAME,
        description=(
            "Correct long-term memory when the user points out that something "
            "remembered is wrong or outdated. Finds the stored note closest to the "
            "old statement and replaces it with the corrected one, keeping history."
        ),
        args_schema=_MemoryReviseArgs,
    )
    read_log_tool = StructuredTool.from_function(
        coroutine=handlers.read_log,
        name=MEMORY_READ_LOG_NAME,
        description=(
            "Read the raw conversation log verbatim for a range of turns. Use it "
            "to recall the exact scene behind a memory note: notes returned by "
            "memory_search carry their source range as «(ходы X–Y)». The raw log "
            "keeps every detail that distilled notes may have compressed away."
        ),
        args_schema=_MemoryReadLogArgs,
    )
    track_tool = StructuredTool.from_function(
        coroutine=handlers.track,
        name=MEMORY_TRACK_NAME,
        description=(
            "Track a life measurement or event as one data point in the metric's "
            "time series: weight, sleep hours, workouts, meals — anything "
            "countable the user reports. Use it whenever the user states a "
            "number about themselves or mentions a completed countable event. "
            "For durable statements ('I love spicy food') use memory_save "
            "instead."
        ),
        args_schema=_MemoryTrackArgs,
    )
    stats_tool = StructuredTool.from_function(
        coroutine=handlers.stats,
        name=MEMORY_STATS_NAME,
        description=(
            "Aggregate statistics over tracked life metrics: count, average, "
            "min/max, sum, last value — optionally bucketed by day, week or "
            "month. Use it for questions like 'how much did I sleep on average' "
            "or 'when did I last go to the gym'. Call without arguments to list "
            "all tracked metrics."
        ),
        args_schema=_MemoryStatsArgs,
    )
    return {
        MEMORY_SEARCH_NAME: search_tool,
        MEMORY_SAVE_NAME: save_tool,
        MEMORY_REVISE_NAME: revise_tool,
        MEMORY_READ_LOG_NAME: read_log_tool,
        MEMORY_TRACK_NAME: track_tool,
        MEMORY_STATS_NAME: stats_tool,
    }
