"""ASGI entry-point монолита.

Uvicorn target: `bestfiend.main:app`. Реэкспорт `app` из `bestfiend.app.http`
(там собирается FastAPI с lifespan'ом, который поднимает `CoreRuntime`).
"""

from bestfiend.app.http import app


__all__ = ["app"]
