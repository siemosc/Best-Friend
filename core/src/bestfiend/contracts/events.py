"""Контракты канала ingress↔graph↔telegram: вход графа и outbound-стрим.

Public API:
- `InputEvent` — единый контракт входа в graph (производит ingress — Telegram).
- `AnswerDelta` / `ProgressStep` / `AnswerFinal` / `AnswerReset` — outbound стрим из graph.
- `OutboundEvent` — discriminated union outbound-событий.
- Порты `OutboundEventPublisher` / `OutboundEventSource` — стороны канала доставки.

Контракты живут вне graph/telegram, потому что `InputEvent` пишет ingress
(telegram), читает graph, а `OutboundEvent` — наоборот: пишет graph, читает
telegram.
"""

from datetime import UTC, datetime
from typing import Annotated, Any, Literal, Protocol, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from bestfiend.contracts.artifacts import ArtifactRef


class InputEvent(BaseModel):
    """Единый контракт входного события для orchestration graph.

    Производитель — ingress (сейчас единственный — Telegram); потребитель —
    graph-runtime. Graph использует `processing_mode` для routing.
    """

    processing_mode: Literal["task"] = Field(
        default="task",
        description=(
            "Hint от ingress'а о режиме обработки. `task` — потенциальная "
            "работа: react исполняет цикл с тулами и сам решает, когда "
            "завершить ответом."
        ),
    )

    user_id: UUID = Field(
        description=(
            "UUID пользователя, к которому привязано событие. Ключ для "
            "`control_plane` при получении `UserMetadata` (LLM-конфиги, "
            "allowed MCP-серверы, персональные инструкции)."
        ),
    )

    message: str = Field(
        description=(
            "Текст сообщения/события. Для `user` — оригинальный запрос; "
            "для `system` — человекочитаемое описание произошедшего."
        ),
    )

    channel: str = Field(
        description=(
            "Канал-источник события (например, `telegram`). Уходит в "
            "trace-атрибуты; доставка ответа привязана к request_id, не к каналу."
        ),
    )

    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Канало-специфичные поля, пробрасываемые до delivery "
            "(`chat_id`, `message_id` для telegram и т.п.). "
            "Схема зависит от `channel`."
        ),
    )

    request_id: str = Field(
        min_length=1,
        description=(
            "Уникальный id запроса в пределах процесса (= Langfuse session_id). "
            "Scope обработки, correlation для ingress-delivery."
        ),
    )

    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="UTC-момент создания события на стороне ingress'а.",
    )

    attached_artifacts: list[ArtifactRef] = Field(
        default_factory=list,
        description=(
            "Артефакты, загруженные источником вместе с запросом "
            "(например, файлы от пользователя через telegram). "
            "Заполнение поля — задача источника (gateway)."
        ),
    )


# ── Outbound stream events ───────────────────────────────────────────
# Discriminated union по полю `type`. Frozen models для безопасной публикации.


class AnswerDelta(BaseModel):
    """Дельта ответа: инкрементальный кусок текста для streaming.

    Telegram-доставка аккумулирует дельты per request_id и пушит драфт через
    `send_message_draft(text=accumulated)`. По каналу летит только новая дельта.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    type: Literal["answer_delta"] = "answer_delta"
    request_id: str
    delta: str


class ProgressStep(BaseModel):
    """Шаг прогресса: append-only сообщение «что я сейчас делаю»."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    type: Literal["progress_step"] = "progress_step"
    request_id: str
    text: str


class AnswerFinal(BaseModel):
    """Финал ответа: маркер окончания стрима + полный финальный текст и
    опциональные attachments (artifact refs).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    type: Literal["answer_final"] = "answer_final"
    request_id: str
    text: str
    attachments: list[ArtifactRef] = Field(default_factory=list)


class AnswerReset(BaseModel):
    """Сброс накопленного стрим-ответа: предыдущий сегмент оказался preface.

    Эмитится, когда react-шаг свернул в tool_calls — значит стримленный до этого
    content был не ответом, а preface. Consumer обнуляет накопитель ответа
    (runtime — streamed_chunks, telegram — draft-буфер), чтобы preface не попал
    в финал.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    type: Literal["answer_reset"] = "answer_reset"
    request_id: str


OutboundEvent = Annotated[
    AnswerDelta | ProgressStep | AnswerFinal | AnswerReset,
    Field(discriminator="type"),
]
"""Discriminated union outbound-событий канала доставки."""


class OutboundEventPublisher(Protocol):
    """Порт производителя outbound-событий (сторона graph).

    Реализация (app.StreamPublisher) владеет очередями per request_id;
    подписка должна быть открыта consumer'ом ДО первого publish.
    """

    async def publish(self, event: OutboundEvent) -> None:
        """Публикует событие в очередь его request_id."""
        ...

    async def close(self, request_id: str) -> None:
        """Закрывает поток запроса (sentinel конца итерации подписчику)."""
        ...


class OutboundEventSubscription(Protocol):
    """Порт подписки на поток одного запроса: async-итерация до закрытия."""

    def __aiter__(self) -> Self: ...

    async def __anext__(self) -> OutboundEvent: ...

    async def close(self) -> None:
        """Освобождает подписку (идемпотентно)."""
        ...


class OutboundEventSource(Protocol):
    """Порт потребителя outbound-событий (сторона telegram): открытие подписки."""

    def open(self, request_id: str) -> OutboundEventSubscription:
        """Открывает подписку на события запроса (строго до старта graph-таска)."""
        ...
