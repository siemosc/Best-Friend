"""Структуры выхода Reflector (structured output — схема является частью промпта).

LLM оперирует индексами строк журнала из текста промпта; маппинг индексов
в note_id выполняет сервис.
"""

from typing import Literal

from pydantic import BaseModel, Field


class Reflection(BaseModel):
    """Сводная запись: несколько строк журнала об одной линии событий → одна."""

    content: str = Field(
        description=(
            "Плотная сводная запись: итог линии событий с конкретными именами, "
            "значениями и ключевыми датами прямо в тексте. Язык — язык журнала."
        )
    )
    source_indexes: list[int] = Field(
        description="Индексы строк журнала, свёрнутых в эту запись."
    )
    weight: Literal["high", "mid", "low"] = Field(
        description=(
            "high — решения и факты о пользователе; mid — рабочий контекст; "
            "low — фоновые детали."
        )
    )


class ReflectorOutput(BaseModel):
    """Полный выход одного прогона Reflector."""

    reflections: list[Reflection] = Field(
        default_factory=list,
        description="Сводные записи; пусто, если сворачивать нечего.",
    )
    evict_indexes: list[int] = Field(
        default_factory=list,
        description=(
            "Индексы строк, чья ценность исчерпана: разовые детали, шаги, "
            "превзойдённые ходом событий. Уходят из журнала без свёртки."
        ),
    )
