"""Структуры выхода Reconciler (structured output — схема является частью промпта).

LLM оперирует индексами кандидатов и соседей из текста промпта; маппинг
индексов в note_id выполняет сервис.
"""

from typing import Literal

from pydantic import BaseModel, Field


class ReconcileDecision(BaseModel):
    """Решение по одному кандидату."""

    candidate_index: int = Field(
        description="Индекс кандидата из списка во входном сообщении."
    )
    action: Literal["add", "supersede", "noop", "contradict"] = Field(
        description=(
            "add — новое знание, записать; noop — уже записано (сосед утверждает "
            "то же); supersede — обновляет соседа (тот же предмет, новое значение); "
            "contradict — несовместимо с соседом и по тексту не определить, что вернее."
        )
    )
    target_index: int | None = Field(
        default=None,
        description=(
            "Для supersede и contradict — индекс соседа из списка этого кандидата. "
            "Для add и noop — null."
        ),
    )
    pin: bool = Field(
        default=False,
        description=(
            "true — знание попадает в постоянный профиль, видимый ассистенту в "
            "каждом ответе: устойчивые предпочтения, правила работы, факты о самом "
            "пользователе. false — знание остаётся в архиве и находится поиском."
        ),
    )
    pin_section: Literal["identity", "preferences", "relationships", "rules"] | None = (
        Field(
            default=None,
            description=(
                "Секция профиля при pin=true: identity — кто пользователь; "
                "preferences — что и как он любит; relationships — люди и проекты "
                "вокруг; rules — правила работы ассистента."
            ),
        )
    )


class ReconcileOutput(BaseModel):
    """Полный выход Reconciler: ровно одно решение на каждого кандидата."""

    decisions: list[ReconcileDecision] = Field(
        default_factory=list,
        description="Ровно одно решение на каждого кандидата по его candidate_index.",
    )
