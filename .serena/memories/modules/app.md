# Модуль app/ — композиционный корень и HTTP-поверхность

`core/src/bestfiend/app/`. Сборка CoreRuntime, lifecycle, БД, весь HTTP, StreamPublisher.

## Boot

`main.py` → uvicorn → `app/http.py create_app()` (FastAPI + lifespan) → `app/lifecycle.py make_lifespan()` → `build_runtime()` (sync, без I/O) → `CoreRuntime.start()` (async). В тестах create_app принимает fake-runtime параметром.

## CoreRuntime (app/runtime.py) — транзакционный lifecycle

Dataclass в `app.state.runtime`; роуты достают его через `get_runtime(request)` (app/routes/dependencies.py). Все зависимости композиции **обязательны** — `memory_runtime`, `artifacts_runtime`, `dashboard_service`, `assistant_service`, `mcp_management_service` не бывают None, проверять их в роутах не нужно. Optional — только lifecycle-handles (stream_publisher, graph_runtime, langfuse_client, telegram_runtime), заполняются в start(). Один экземпляр = один цикл запуска (`_started`/`_stopped`, повторный start остановленного — RuntimeError).

- **start()**: db.connect (миграции) → memory.start → artifacts.start → Langfuse → StreamPublisher → build_graph → GraphRuntime → Telegram. Частичный сбой → откат уже стартовавшего через AsyncExitStack.
- ⚠️ Rollback-коллбэки регистрируются ДО старта ресурсов — схема работает только потому, что все stop()/disconnect() идемпотентны и терпят нестартовавшее состояние. Новый ресурс с нетерпимым stop() сломает rollback молча — проверяй при добавлении.
- **stop()** — идемпотентен; порядок зафиксирован shutdown-стеком: telegram → dashboard.aclose → memory.stop_scheduling → background_tasks.shutdown(10s) → memory.stop → artifacts.stop → db.disconnect → langfuse (flush + shutdown).
- Fail-open при старте: нет Langfuse-ключей → tracing off (client с tracing_enabled=False); нет `TELEGRAM_BOT_TOKEN` → бот пропущен.

Ingress-точка событий: `CoreRuntime.publish_input_event(*, event, request_correlation)` — root-span "bestfiend-core.ingress" + propagate_attributes (user_id, session_id=request_id). Единственный вызывающий — TelegramBot; подписка `stream_publisher.open(request_id)` обязана быть открыта caller'ом ДО вызова.

## errors.py

`AppError` → `CoreRuntimeNotInitializedError`: обращение к runtime до того, как lifespan положил его в app.state. Сырых RuntimeError в роутах нет.

## Env (app/settings.py)

`POSTGRES_HOST/PORT/DB/USER/PASSWORD` (дефолты localhost/5433/bestfiend/bestfiend/changeme), `POSTGRES_POOL_MIN_SIZE/MAX_SIZE` (5/20), `POSTGRES_AUTO_MIGRATE` (true), `LANGFUSE_ENABLED/PUBLIC_KEY/SECRET_KEY/BASE_URL/FLUSH_INTERVAL`, `CORE_PORT` (8010, читает `__main__.py`).

## БД (app/db.py)

`CorePostgreSQLClient` — schema-owner. Пул asyncpg с `search_path=core,public`. Миграции на connect: advisory lock **748303**, каталог `core/scripts/migrations/*.sql` sorted, реестр `core._migrations`. Legacy seed: core.users существует, а снапшот `001_initial_schema.sql` не отмечен → INSERT имени в _migrations без применения DDL. DB-порт для control_plane — `control_plane/db.py`.

## HTTP (app/http.py + app/routes/)

**app владеет всей HTTP-поверхностью.** Доменные пакеты роутеров не держат: `memory` и `artifacts` отдают домен, `control_plane` — сервисы. Правило простое: роутер, его request/response DTO и exception-handler лежат рядом, в `app/routes/`.

- `dependencies.py` — `get_runtime(request) -> CoreRuntime` + guards `require_session` / `require_admin` / `require_self_or_admin`. Типизация прямая, без Protocol: раз HTTP целиком здесь, порт в control_plane не нужен.
- `cookies.py` — read/set/clear_session_cookie (транспортная механика сессии).
- `error_handlers.py` — `ErrorResponse` (единый error-контракт) + маппинг UserError/AuthError/AssistantConfigError.
- `user_views.py` — `UserResponse` + profile_payload/profile_response, общее для users и auth.
- `users.py`, `auth.py`, `assistant.py`, `dashboard.py` — роутер + свои request-DTO.
- `mcp/` — router.py + contracts.py.
- `memory/` — router/overview/notes/activity + dependencies; доменная логика в `memory/web_facade/` (`mem:modules/memory`), включая сборку NoteView. Роутер здесь, а не в memory, чтобы memory не импортировал control_plane ради auth-guard.
- `artifacts.py` — `POST /internal/artifacts` (trusted-create; header `x-bestfiend-user-id` — константа в самом файле).
- `GET /health` — liveness.

Пользовательские роуты — session-cookie (`require_session`/`require_admin`).

## StreamPublisher (app/stream_publisher.py)

Владелец очередей событий per request_id. Инвариант против гонки: `open(request_id)` — **sync и ДО** `create_task(graph)`, publish всегда найдёт очередь. `publish(OutboundEvent)` → очередь; `close()` пушит sentinel None → конец async-итератора подписки. Повторный open того же request_id → StreamAlreadyOpenError. Publish после close/disconnect → warning + drop, не raise. Safety-net: граф упал до AnswerFinal → публикуется fallback-текст. Порты (contracts/events.py): для graph — `OutboundEventPublisher`, для подписчика — `OutboundEventSource`; реализация обоих здесь.

Потребитель — TelegramBot (in-process подписка) — см. `mem:modules/telegram`.

## primitives/

- `background_tasks.py` — `BackgroundTaskSupervisor`: владелец всех detached-задач процесса (graph persist, memory use_count bump). `create_task(coro)`; `shutdown(timeout_s=10)`: ждёт → отменяет → обязательно ожидает остаток. После shutdown новые задачи отклоняет `BackgroundTaskSupervisorClosedError` (+ `coroutine.close()` против RuntimeWarning). Голых `asyncio.create_task` для фона в модулях не заводить.
- `tokenizer.py count_tokens(text)` — tiktoken cl100k_base (единый счётчик для бюджетов).
