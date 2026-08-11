# BestFiend — карта проекта (корень графа memories)

Модульный монолит AI-ассистента: Telegram-бот + web-админка. Внутренний инструмент DarkLamb-ai (~5–20 юзеров, один хост). Монорепо, per-service Python-окружения (uv). Версия проекта живёт в заголовках коммитов (`vXX.Y.Z - тип: описание`).

## Раскладка репо

- `core/` — монолит (Python, порт 8010). Пакет `core/src/bestfiend/`. Запуск: `uv run python -m bestfiend`.
- `web/` — SvelteKit SPA админка: `mem:web`.
- `infra/` — только `postgres-init/`; сам compose — плоский корневой `docker-compose.yaml` (postgres 5433, seaweedfs; core+web под профилем `app`): `mem:infra`.
- `tools/qa/` — изолированный QA-раннер; `scripts/dev.sh` — one-button dev-стек. Команды: `mem:suggested_commands`.
- `docs/` — design records, статус:
  - `memory_architecture_alt.md` — **актуальный SoT** по памяти (читать при работе над memory/).
  - `modular_monolith_migration.md` — решения + журнал ЗАВЕРШЁННОЙ миграции; §11 trade-offs и критерий contracts/ действуют. Постскриптум в шапке: CF (v29.0.3) и embedding_service (v29.5.2) снесены после миграции.
  - `artifact_architecture.md` — **исторический**, шапка-маркер стоит в самом файле (микросервисы, PG-registry, ContextForge, final_answer) — не источник истины.

## Модули core/src/bestfiend/ (детали — по ссылкам)

- `app/` — boot, CoreRuntime, БД+миграции, HTTP-роуты, StreamPublisher: `mem:modules/app`.
- `graph/` — LangGraph react-агент (init→react⇄tools, error), стриминг, persist: `mem:modules/graph`.
- `memory/` — лог-центричная память (turns/notes, Observer/Reconciler/Reflector/sleep, recall, бюджет): `mem:modules/memory`.
- `control_plane/` — auth/сессии, model_registry (резолв LLM-конфига), assistant-конфиги, MCP-менеджмент, dashboard: `mem:modules/control_plane`.
- `artifacts/` — файлы в SeaweedFS, meta.json, enrichment: `mem:modules/artifacts`.
- `telegram/` — aiogram-бот, draft-стриминг, доставка: `mem:modules/telegram`.
- `mcp/` — fastmcp-клиент, discovery, coercion результатов: `mem:modules/mcp`.
- `ai/` — фабрики LLM/эмбеддингов, провайдер-воркэраунды: `mem:modules/ai`.
- `contracts/` + `primitives/` — кросс-модульные DTO и утилиты (tokenizer, BackgroundTaskSupervisor); сквозной поток запроса и критерий contracts/: `mem:architecture`.

Паттерн модуля: свои `contracts.py`, `errors.py`, `settings.py`, `runtime.py`.

## Сквозные инварианты

- Один event loop: CPU-bound работе в монолите не место (`mem:architecture`).
- Фоновые detached-задачи — только через BackgroundTaskSupervisor, владелец — CoreRuntime (`mem:modules/app`).
- Схема БД `core`, миграции автоприменяются на старте (advisory lock): `mem:db_schema`.
- ⚠️ core.models наполнена руками, снапшот 001 её данных не содержит — не пересоздавать БД без бэкапа таблицы.
- ContextForge удалён (v29.0.3). Доки вычищены в v29.9.4: README без CF, migration-док с постскриптумом, artifact-док помечен историческим. Остался хвост: пустая схема contextforge в живой БД (дроп — backlog); каталог infra/contextforge снесён в v29.14.0.
- LLM-вызовы фоновых пайплайнов памяти — строго вне транзакций БД.
- Внешний вид у юзера: Telegram — единственный чат-канал; web — админка/память, не чат.

Стек: `mem:tech_stack`. Команды: `mem:suggested_commands`. Стиль: `mem:conventions`. DoD: `mem:task_completion`. Схема БД: `mem:db_schema`.