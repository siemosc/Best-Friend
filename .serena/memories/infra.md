# infra — Docker и dev-окружение

Вся инфра — один плоский корневой `docker-compose.yaml` (v29.14.0, `name: bestfiend`): postgres и seaweedfs поднимаются всегда, контейнерные core и web — под профилем `app` (деплой-контур, см. README «Деплой»). Точечный запуск — `docker compose up -d postgres`. Каталог `infra/` содержит только `postgres-init/`.

## PostgreSQL (сервис `postgres`)

- Образ pgvector/pgvector:pg16, контейнер bestfiend-postgres, **наружу порт 5433** (внутри 5432). БД/юзер: bestfiend/bestfiend.
- Пароль — `${POSTGRES_PASSWORD:-changeme}`: одна compose-переменная задаёт пароль и серверу, и core (поверх `core/.env`) — значения не разъезжаются. Дефолт `changeme` — dev; на проде переменную задаёт окружение деплой-сессии или корневой `.env` (в репо его нет).
- Init-скрипт `infra/postgres-init/02-schemas.sql` (выполняется ТОЛЬКО при первой инициализации volume): создаёт схему `core`, ставит роли search_path = core,public. Приложение дублирует search_path через asyncpg server_settings — страховка для старых volume, где роль ещё несёт contextforge,public. На существующем volume схему добавлять руками (docker exec psql; команды — в шапке самого скрипта).
- Хвост: в живой БД пустует схема `contextforge` (ContextForge снесён v29.0.3); дроп — backlog.

## SeaweedFS (сервис `seaweedfs`)

- Образ chrislusf/seaweedfs:**4.40** (пин; до v29.14.0 был `:latest`). Порты: 8333 (S3 API), 8888 (filer, дебаг).
- Dev-режим без identity-конфига — принимает любые креды; bucket создаёт ArtifactsRuntime.start(). Для prod — `-s3.config` с accessKey/secretKey = ARTIFACT_S3_*.

## dev.sh (scripts/dev.sh)

Дев-стек = 2 контейнера (postgres, seaweedfs) + 2 процесса: core (порт 8010, `CORE_PORT=8010 uv run python -m bestfiend`) + web (pnpm dev, 5173). Флаги: `--infra-only`, `--services-only`, `--no-ui`. Скрипт ждёт готовности обоих контейнеров (pg_isready + docker health seaweedfs, до 30с каждый). Логи процессов: `logs/dev/<name>.log`; PID-файл logs/dev/.pids; Ctrl+C гасит процессы, контейнеры остаются. Windows-aware cleanup (taskkill).

## Langfuse

Self-hosted стек удалён из репо в v29.14.0 (было: профиль `observability`, 6 контейнеров). Трейсинг в коде жив: клиент langfuse v4 в core, ключи в env core (`mem:modules/app`), без ключей — тихий no-op; бэкенд — cloud или внешний self-hosted инстанс.

## Прочее

- Поисковый бэкенд (SearXNG + trafilatura) — отдельный репо github.com/vakovalskii/searcharvester, поднимается своим compose, подключается как MCP.
