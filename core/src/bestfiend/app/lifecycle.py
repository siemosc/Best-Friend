"""Lifecycle core: поднимает CoreRuntime (DB-pool) на старте, глушит на стопе.

Фабрика `make_lifespan` повторяет паттерн control_plane: при переданном
runtime (тесты со stub) он только кладётся в `app.state` без сборки и
без DB; иначе runtime собирается из окружения и поднимается.
"""

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager

from fastapi import FastAPI

from bestfiend.app.runtime import CoreRuntime, build_runtime


def make_lifespan(
    runtime: CoreRuntime | None = None,
) -> Callable[[FastAPI], AbstractAsyncContextManager[None]]:
    """Строит lifespan. runtime=None → собрать и поднять; иначе — внедрить (тесты)."""

    @asynccontextmanager
    async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
        if runtime is not None:
            app.state.runtime = runtime
            yield
            return

        built = build_runtime()
        await built.start()
        app.state.runtime = built
        try:
            yield
        finally:
            await built.stop()

    return _lifespan
