"""Entrypoint-шим: поднимает HTTP-приложение core через uvicorn."""

import os

import uvicorn

from bestfiend.app.http import app


_DEFAULT_HOST = "0.0.0.0"  # nosec B104 — контейнер принимает внешние подключения
_DEFAULT_PORT = 8010


def main() -> None:
    """Запускает uvicorn с приложением core (порт — env CORE_PORT)."""
    port = int(os.environ.get("CORE_PORT", _DEFAULT_PORT))
    uvicorn.run(app, host=_DEFAULT_HOST, port=port)


if __name__ == "__main__":
    main()
