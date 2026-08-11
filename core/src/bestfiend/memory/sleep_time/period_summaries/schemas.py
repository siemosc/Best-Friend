"""Structured output недельной сводки."""

from pydantic import BaseModel, Field


class PeriodSummaryOutput(BaseModel):
    """Сводка недели из наблюдений."""

    content: str = Field(
        description=(
            "Плотная сводка недели: главные события, решения и результаты с "
            "датами и конкретикой. Язык — язык наблюдений."
        )
    )
