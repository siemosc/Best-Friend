"""Per-request publisher outbound-событий графа (in-process очереди).

Один `StreamPublisher` живёт в runtime; на каждый request_id открывается
изолированная `asyncio.Queue` через `open()`. Producer (`graph.streaming.invoke_graph`)
пишет события через `publish()`, consumer (telegram `OutboundDelivery`) читает
через `StreamSubscription`.

Lifecycle:
    sub = publisher.open(request_id)         # sync, регистрация ДО старта graph
    asyncio.create_task(run_graph())          # параллельно publish'ит
    async for event in sub: ...               # читает до sentinel
    await sub.close()                         # idempotent

Race-free: регистрация очереди — синхронная, до того как graph_task стартует.
Гарантирует что любой `publish()` найдёт queue.
"""

import asyncio
from typing import Self

from loguru import logger

from bestfiend.contracts.events import OutboundEvent


class StreamAlreadyOpenError(RuntimeError):
    """Подписка для данного request_id уже открыта."""


class StreamPublisher:
    """Owner-сторона: хранит очереди per request_id, публикует события."""

    __slots__ = ("_queues",)

    def __init__(self) -> None:
        # Sentinel None — маркер закрытия канала.
        self._queues: dict[str, asyncio.Queue[OutboundEvent | None]] = {}

    def open(self, request_id: str) -> "StreamSubscription":
        """Регистрирует новую подписку под request_id.

        SYNC. Должен вызываться ДО запуска graph_task — гарантирует, что
        любой последующий `publish()` найдёт очередь.

        Raises:
            StreamAlreadyOpenError: если подписка уже открыта.
        """
        if request_id in self._queues:
            raise StreamAlreadyOpenError(
                f"stream already open for request_id={request_id}"
            )
        queue: asyncio.Queue[OutboundEvent | None] = asyncio.Queue()
        self._queues[request_id] = queue
        return StreamSubscription(
            publisher=self,
            request_id=request_id,
            queue=queue,
        )

    async def publish(self, event: OutboundEvent) -> None:
        """Публикует событие в очередь request_id.

        Если очереди нет (закрыта / не существовала) — log warning + drop.
        Не raise — late publish после client disconnect считается нормой.
        """
        queue = self._queues.get(event.request_id)
        if queue is None:
            logger.warning(
                "StreamPublisher: publish to unknown/closed request_id={} type={}",
                event.request_id,
                event.type,
            )
            return
        await queue.put(event)

    async def close(self, request_id: str) -> None:
        """Закрывает подписку: пушит sentinel и удаляет очередь.

        Idempotent: повторный close — no-op.
        """
        queue = self._queues.pop(request_id, None)
        if queue is None:
            return
        await queue.put(None)


class StreamSubscription:
    """Handle одной подписки. Async iterator над очередью.

    Стандартный AsyncIterator protocol: sync `__aiter__` + async `__anext__`.
    Завершается через sentinel None (`close()` со стороны publisher либо
    собственный `close()`).
    """

    __slots__ = ("_publisher", "_request_id", "_queue", "_closed")

    def __init__(
        self,
        *,
        publisher: StreamPublisher,
        request_id: str,
        queue: asyncio.Queue[OutboundEvent | None],
    ) -> None:
        self._publisher = publisher
        self._request_id = request_id
        self._queue = queue
        self._closed = False

    @property
    def request_id(self) -> str:
        return self._request_id

    def __aiter__(self) -> Self:
        return self

    async def __anext__(self) -> OutboundEvent:
        event = await self._queue.get()
        if event is None:
            raise StopAsyncIteration
        return event

    async def close(self) -> None:
        """Закрывает подписку через publisher (idempotent)."""
        if self._closed:
            return
        self._closed = True
        await self._publisher.close(self._request_id)
