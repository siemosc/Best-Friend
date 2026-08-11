"""Конфигурация графа оркестрации: env settings и graph-level utilities."""

from typing import Final

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


# Дефолты бюджетов рекурсии — один источник для GraphSettings и GraphContext.
GRAPH_RECURSION_LIMIT_DEFAULT: Final = 100
CHILD_RECURSION_LIMIT_DEFAULT: Final = 25
SOFT_GATE_LIMIT_DEFAULT: Final = 3
MAX_RECURSION_DEPTH_DEFAULT: Final = 2


# -------------------------------------------------------------------
# Env settings для model ID и graph-level параметров
# -------------------------------------------------------------------

_ENV_SETTINGS = SettingsConfigDict(
    env_file=".env",
    env_file_encoding="utf-8",
    extra="ignore",
    # model_id задевает protected namespace pydantic `model_` — снимаем защиту.
    protected_namespaces=(),
)


class ModelIDSettings(BaseSettings):
    """ID единой модели графа. Резолвится через control_plane."""

    model_id: str = Field(
        "orchestrator-default",
        validation_alias="MODEL_ID",
    )
    model_config = _ENV_SETTINGS


# -------------------------------------------------------------------
# Graph-level settings
# -------------------------------------------------------------------


class GraphSettings(BaseSettings):
    """Настройки поведения графа: нативная передача картинок + бюджеты рекурсии."""

    # Кап нативных картинок из ИСТОРИИ (текущий ход гидрируется целиком, без капа).
    vision_max_history_images: int = Field(
        6,
        validation_alias="VISION_MAX_HISTORY_IMAGES",
    )
    # Порог по сырым байтам артефакта (до base64, на проводе ×1.37).
    vision_max_image_bytes: int = Field(
        5_242_880,
        validation_alias="VISION_MAX_IMAGE_BYTES",
    )
    # Общий бюджет graph-step'ов одного прогона (recursion_limit langgraph).
    graph_recursion_limit: int = Field(
        GRAPH_RECURSION_LIMIT_DEFAULT,
        validation_alias="GRAPH_RECURSION_LIMIT",
        gt=0,
    )
    # Бюджет дочернего прогона delegate_subtask.
    child_recursion_limit: int = Field(
        CHILD_RECURSION_LIMIT_DEFAULT,
        validation_alias="GRAPH_CHILD_RECURSION_LIMIT",
        gt=0,
    )
    # Порог graceful-финала: react сворачивается при remaining_steps <= лимита.
    soft_gate_limit: int = Field(
        SOFT_GATE_LIMIT_DEFAULT,
        validation_alias="GRAPH_SOFT_GATE_LIMIT",
        ge=0,
    )
    # Максимальная глубина само-рекурсии delegate_subtask.
    max_recursion_depth: int = Field(
        MAX_RECURSION_DEPTH_DEFAULT,
        validation_alias="GRAPH_MAX_RECURSION_DEPTH",
        ge=0,
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )
