"""Structured output генерации вопроса-пробы."""

from pydantic import BaseModel, Field


class ProbeOutput(BaseModel):
    """Проба качества recall: вопрос с известным ответом."""

    question: str = Field(
        description=(
            "Вопрос, на который запись отвечает: как его задал бы пользователь, "
            "вспоминая это спустя недели. Язык — язык записи."
        )
    )
