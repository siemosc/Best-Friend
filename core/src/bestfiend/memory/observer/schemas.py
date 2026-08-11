"""Структуры выхода Observer (structured output — схема является частью промпта)."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class Observation(BaseModel):
    """Датированная запись журнала: что произошло, что решили, что выяснили."""

    content: str = Field(
        description=(
            "Плотный самодостаточный текст записи: конкретные имена, значения и "
            "причины прямо в тексте. Одно событие — одна запись, 1-2 предложения."
        )
    )
    event_time: datetime | None = Field(
        default=None,
        description=(
            "Абсолютное время события, если в тексте есть явная временная привязка. "
            "Для вневременных записей — null."
        ),
    )
    entities: list[str] = Field(
        default_factory=list,
        description="Сущности, к которым относится запись (имена из известных или new_entities).",
    )
    subject: Literal["user", "agent", "world"] = Field(
        description=(
            "О ком запись: user — пользователь и его жизнь; agent — поведение "
            "и работа самого ассистента; world — внешний мир, не привязанный "
            "лично к пользователю."
        )
    )
    weight: Literal["high", "mid", "low"] = Field(
        description=(
            "high — решения, изменения планов, факты о пользователе; "
            "mid — рабочий контекст и результаты; low — фоновые детали."
        )
    )


class FactCandidate(BaseModel):
    """Кандидат в долгосрочное знание — останется истинным и полезным через месяцы."""

    content: str = Field(
        description="Самодостаточная формулировка знания, понятная вне контекста дня."
    )
    kind: Literal["fact", "preference", "rule"] = Field(
        description=(
            "fact — устойчивый факт о мире пользователя; preference — предпочтение "
            "пользователя; rule — правило работы ассистента из обратной связи."
        )
    )
    subject: Literal["user", "agent", "world"] = Field(
        description=(
            "О ком знание: user — сам пользователь и его жизнь; agent — поведение "
            "и работа ассистента; world — внешний мир, не привязанный лично "
            "к пользователю."
        )
    )
    entities: list[str] = Field(
        default_factory=list,
        description="Сущности, к которым относится знание.",
    )
    event_time: datetime | None = Field(
        default=None,
        description="Абсолютное время, с которого знание истинно, если оно названо в тексте.",
    )


class ObserverOutput(BaseModel):
    """Полный выход одного прогона Observer. Пустые списки — валидный результат."""

    observations: list[Observation] = Field(
        default_factory=list,
        description="Записи журнала, извлечённые из фрагмента; пусто — валидный результат.",
    )
    candidates: list[FactCandidate] = Field(
        default_factory=list,
        description="Кандидаты в долгосрочное знание; пусто — валидный результат.",
    )
    new_entities: list[str] = Field(
        default_factory=list,
        description=(
            "Имена сущностей, которых ещё нет в списке известных, но вокруг которых "
            "в диалоге явно копится контекст."
        ),
    )
