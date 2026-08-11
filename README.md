# BestFiend

Модульный монолит для AI-ассистента.

## Раскладка

| Компонент | Порт | Инфра |
|---|---|---|
| `core/` — модульный монолит (control_plane, graph, telegram, memory, artifacts) | 8010 | PostgreSQL |
| `web/` — SvelteKit SPA (dev) | 5173 | — |

```text
BestFiend/
  core/                # модульный монолит (Python, FastAPI, aiogram, LangGraph)
  infra/               # postgres-init (инициализация схемы БД)
  web/                 # SvelteKit frontend
  scripts/dev.sh       # one-button dev-стек
  tools/qa/            # изолированный QA-tooling (ruff, pyright, etc.)
  docs/                # документация
```

## One-Button Start

Полный dev-стек (Docker + core + web):

```bash
bash scripts/dev.sh
```

Без UI:

```bash
bash scripts/dev.sh --no-ui
```

Только сервисы без Docker:

```bash
bash scripts/dev.sh --services-only
```

## Per-Service окружения

Каждый компонент с Python-кодом имеет собственный `.venv` и `uv.lock`.

```bash
cd core && uv sync
```

## Docker

Вся инфра — в одном плоском `docker-compose.yaml`: postgres + seaweedfs всегда,
core + web под профилем `app` (деплой). Пароль postgres — `${POSTGRES_PASSWORD:-changeme}`:
дефолт для dev, на проде задаётся окружением деплой-сессии или корневым `.env`.

```bash
# Инфра: postgres + seaweedfs (дефолт)
docker compose up -d

# Точечно один сервис
docker compose up -d postgres
```

## Деплой (профиль app)

Контейнерный контур приложения: `core` (порт 8010) + `web` — Caddy со статикой SPA
и прокси `/api/*` → core (префикс срезается, как в dev vite-proxy). Web слушает
только `127.0.0.1:8011` — наружу его выводит reverse-proxy хоста (Tailscale Funnel).

```bash
# Перед стартом: заполнить core/.env (минимум TELEGRAM_BOT_TOKEN, PUBLIC_BASE_URL,
# AUTH_COOKIE_SECURE=true за HTTPS; адреса postgres/seaweedfs compose подставит сам).
docker compose --profile app up -d --build
```

OAuth2 MCP-серверов требует публичного HTTPS: `PUBLIC_BASE_URL` должен смотреть на
внешний адрес web (redirect_uri = `{PUBLIC_BASE_URL}/api/mcp/oauth/callback`).

Остановка всего:

```bash
docker compose --profile "*" down
```

## QA

Изолированный QA-tooling в `tools/qa/.venv` (ruff + pyright + bandit + vulture + radon).

```bash
# Установить QA-venv (один раз)
uv sync --project tools/qa

# Дефолт — только цели, затронутые git diff
uv run --project tools/qa python tools/qa/dev_code_fix.py

# Полный прогон
uv run --project tools/qa python tools/qa/dev_code_fix.py --all

# Конкретный сервис, конкретный набор проверок
uv run --project tools/qa python tools/qa/dev_code_fix.py --service core lint
uv run --project tools/qa python tools/qa/dev_code_fix.py --service core types
```

## Тесты

```bash
uv run --directory core pytest
```
