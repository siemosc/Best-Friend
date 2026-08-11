# Архитектура: поток запроса и кросс-модульные контракты

Монолит = один процесс, один event loop. Осознанные trade-offs (из docs/modular_monolith_migration.md, не переоткрывать): fault-isolation принесена в жертву (компенсатор — supervisor + аккуратный lifecycle); любой новый CPU-bound модуль обязан уходить за процесс-границу (прецедент — снесённый embedding_service с torch).

## Сквозной поток запроса

```
Telegram (bot.py) ──→ InputEvent → CoreRuntime.publish_input_event
                          (Langfuse root-span + bind request_correlation)
        ↓
GraphRuntime.process_input_event:
  параллельно: memory.search (log_tail+journal+profile+recall, бюджет от окна модели)
             + MCP discovery (control_plane resolve → discover_servers)
  → OrchestrationState + GraphContext (модель из ModelRegistry → build_chat_model)
  → invoke_graph (graph/streaming.py): astream → OutboundEvent'ы в StreamPublisher
        ↓                                   ↓
  AnswerDelta / ProgressStep /        подписчик: TelegramBot → OutboundDelivery
  AnswerReset / AnswerFinal           (draft → anchor → финал; открыт ДО graph task)
        ↓
  фоновый persist_turn (graph/persist.py) → memory.write (turns + Observer/sleep триггеры)
```

Detached-задачи (persist, use_count bump) — только через `BackgroundTaskSupervisor` (primitives/background_tasks.py): владелец — CoreRuntime, shutdown ждёт → отменяет → дожидается. Голый `asyncio.create_task` для фона в модулях не заводить; известное исключение-долг — media-group flush в telegram (`mem:backlog`).

Telegram — единственный ingress InputEvent'ов. Контракт OutboundEvent канало-нейтрален (StreamPublisher не знает подписчика), но живой подписчик один — TelegramBot; SSE-вход/выход для web снесён рефакторингом 2026-07 как мёртвый (web — админка, не чат).

## bestfiend/contracts/ — кросс-модульные DTO

Критерий попадания (зафиксирован при миграции): чистый pass-through DTO, протекающий через модули, которые НЕ вызывают владеющую capability. Домен-ошибки, request-DTO модулей — остаются в модулях.

- `events.py` — InputEvent (processing_mode, user_id, message, channel, metadata, request_id, attached_artifacts) и OutboundEvent = AnswerDelta | ProgressStep | AnswerFinal(text, attachments) | AnswerReset (discriminated union по type).
- `user_environment.py` — UserEnvironment (timezone/city/country) для LLM-промптов и scheduling.
- `artifacts.py` — ArtifactRef (см. `mem:modules/artifacts`).
- `mcp.py` — ResolvedMcpServer (портовый DTO: control_plane резолвит, mcp потребляет; разрыв цикла mcp↔control_plane, рефакторинг 2026-07).
- RequestCorrelation (request_id + user_id) живёт у единственного потребителя — `telegram/request_correlation.py`; в `contracts/` его нет: нормализация на ingress не пересекает границ модулей. Header `x-bestfiend-user-id` — константа в app/routes/artifacts.py.

Порты (протоколы) для инверсии зависимостей: `OutboundEventPublisher`/`OutboundEventSource` (graph ↔ app), `McpServerResolver` (graph ↔ control_plane) — введены рефакторингом 2026-07, циклы модулей разорваны, матрица импортов чиста.

## Идентичность и авторизация ingress

Telegram: resolve_or_create_by_telegram (auto-провижининг, status=pending до активации админом). Web: session cookie. Привязка каналов: binding code из бота (`/web`) → POST /auth/bind.

## Наблюдаемость

Langfuse v4 нативно: root-span на publish_input_event, CallbackHandler на graph invoke (lazy provider, резолвится внутри защищённой зоны invoke_graph), memory-пайплайны — свои спаны через memory/tracing.py. Без ключей — тихий no-op. Дефолт LANGFUSE_BASE_URL — cloud.

Детали модулей: `mem:modules/app`, `mem:modules/graph`, `mem:modules/memory`, `mem:modules/telegram`, `mem:modules/mcp`, `mem:modules/control_plane`, `mem:modules/artifacts`, `mem:modules/ai`.