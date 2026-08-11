"""Тестовый двойник издателя потоковых событий graph."""

from typing import Any


class StreamPublisherFake:
    """Записывает вызовы publish и close для последующих проверок."""

    def __init__(self) -> None:
        self.published: list[Any] = []
        self.closed: list[str] = []

    async def publish(self, event: Any) -> None:
        self.published.append(event)

    async def close(self, request_id: str) -> None:
        self.closed.append(request_id)
