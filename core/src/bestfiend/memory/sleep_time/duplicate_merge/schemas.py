"""Structured output проверки заметок на дублирование."""

from pydantic import BaseModel, Field


class MergeDecision(BaseModel):
    """Решение по одной паре почти-дублей."""

    pair_index: int = Field(description="Индекс пары из списка во входном сообщении.")
    merge: bool = Field(
        description=(
            "true — записи говорят об одном и том же, объединить; false — записи "
            "о разном, оставить обе."
        )
    )
    merged_content: str | None = Field(
        default=None,
        description=(
            "Для merge=true: объединённая формулировка, сохраняющая всю "
            "конкретику обеих записей без повторов."
        ),
    )


class MergeOutput(BaseModel):
    """Решения по всем парам батча."""

    decisions: list[MergeDecision] = Field(
        default_factory=list,
        description="Ровно одно решение на каждую пару по её pair_index.",
    )
