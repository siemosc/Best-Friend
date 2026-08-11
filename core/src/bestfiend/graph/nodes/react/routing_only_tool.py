"""Заглушка исполнения routing-only тулов.

`delegate_subtask` и `send_artifact_to_user` объявлены как `StructuredTool`, но
исполняются спец-ветками tools-ноды: ей нужен доступ к графу и state. Собственный
coroutine тула — защита от прямого вызова, а не рабочий путь.
"""

from collections.abc import Awaitable, Callable
from typing import Any


class RoutingOnlyToolInvokedError(RuntimeError):
    """Routing-only тул вызван через coroutine вместо tools-ноды."""


def unreachable_tool_callback(tool_name: str) -> Callable[..., Awaitable[str]]:
    """Возвращает coroutine-заглушку, падающую при прямом вызове тула."""

    async def _unreachable(**_: Any) -> str:
        raise RoutingOnlyToolInvokedError(
            f"{tool_name} исполняется в tools-ноде, не через coroutine"
        )

    return _unreachable
