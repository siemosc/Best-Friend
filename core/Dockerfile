# syntax=docker/dockerfile:1

# --- Стадия сборки: ставим зависимости в venv через uv ---
FROM python:3.11-slim-bookworm AS build

# uv из официального образа, пин по версии (не latest)
COPY --from=ghcr.io/astral-sh/uv:0.11.30 /uv /uvx /bin/

# Системный Python обоих стейджей (без докачки интерпретаторов uv),
# байткод компилируем заранее, копирование вместо hardlink под cache-mount.
ENV UV_PYTHON_DOWNLOADS=0 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# Только манифесты через bind-mount — слой зависимостей кешируется отдельно от исходников.
# --no-install-project: ставим зависимости, но не сам проект (его запускаем через PYTHONPATH).
# --frozen: лок-файл берём как есть, без пересчёта.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    uv sync --frozen --no-dev --no-install-project

# --- Стадия рантайма: минимальный образ без uv и тулчейна ---
FROM python:3.11-slim-bookworm AS runtime

# Non-root пользователь с явными UID/GID
RUN groupadd -r app && useradd --no-log-init -r -g app -u 10001 app

# PYTHONUNBUFFERED — stdout/stderr сразу в логи (loguru пишет в stdout).
# PYTHONPATH=/app/src — пакет bestfiend не установлен в site-packages
# (--no-install-project), импортируется прямо из исходников.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH="/app/src" \
    CORE_PORT=8010

WORKDIR /app

# venv с зависимостями из стадии сборки
COPY --from=build --chown=app:app /app/.venv /app/.venv
# Исходники пакета и SQL-миграции. db.py ищет миграции по parents[3] от
# src/bestfiend/app/db.py, т.е. в /app/scripts/migrations — раскладку сохраняем.
COPY --chown=app:app src /app/src
COPY --chown=app:app scripts /app/scripts

# HEALTHCHECK читает /health рантайм-питоном: в slim нет ни curl, ни wget.
# Порт берём из CORE_PORT (exec-form не разворачивает shell-переменные — читаем в самом Python).
HEALTHCHECK --interval=30s --timeout=3s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import os,urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('CORE_PORT','8010')+'/health').status==200 else 1)"]

USER app
EXPOSE 8010
ENTRYPOINT ["python", "-m", "bestfiend"]
