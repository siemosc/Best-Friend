# Модуль memory/ — лог-центричная память

`core/src/bestfiend/memory/`. Дизайн-док: `docs/memory_architecture_alt.md` (актуальный SoT). Схема таблиц: `mem:db_schema`. Дизайн реализован полностью (2026-06); версионных ярлыков V1/V2 в коде и доках нет.

Идея: неподвижный append-only лог (turns) — источник истины; notes/entities/журнал/профиль — перестраиваемые индексы над ним. LLM только на write path (фоном), read path без LLM.

Раскладка вертикальная: пакет на домен, имя пакета = имя того, чем он владеет. Репозитории названы в единственном числе (`NoteRepository`, `TurnRepository`, `EntityRepository`, `MeasurementRepository`, `WatermarkRepository`, `ProbeRepository`, `MemoryOperationLogRepository`).

## Публичный API (runtime.py)

`create_memory_runtime(model_config_loader)` (без I/O) → `runtime.start()` (пул + embedder/observer/sleep, fail-soft по конфигам). Наружу: `search(user_id, runtime, current_message, budget, background_tasks)` → MemoryContext (log_tail: list[BaseMessage], journal/profile/recall: str); `write(user_id, request, runtime)` — persist turn + фоновые триггеры; `build_memory_tools(runtime, user_id)` — тулы для графа. Остановка: `stop_scheduling()` (только sleep-таймеры, для порядка shutdown CoreRuntime) и `stop()` (stop_scheduling + закрытие пула). Модуль не импортирует control_plane и app — HTTP-роутер живёт в app (`mem:modules/app`).

## Read path (search_pipeline.py)

4 ветки параллельно, каждая fail-soft через `_run_branch_fail_soft` (сбой → дефолт + warning): log_tail (обязательная), journal (in_journal-заметки), profile (pinned по секциям), recall (recall_notes с gate). use_count-bump найденных заметок — фоновая задача через BackgroundTaskSupervisor (передаётся параметром search; глобального набора задач в модуле нет).

**recall/** — гибрид без LLM: 4 списка кандидатов (vector KNN, FTS+trgm, теги сущностей, time-фильтр) → RRF-фузия (K=60) → gate (порог: similarity ИЛИ entity_hit ИЛИ time_hit; не прошёл — блока нет вовсе) → резка по бюджету. Выдача несёт span ходов (мост к memory_read_log).

**budget.py** — `plan_read_budget(window, output_reserve, input_tokens)` → ReadBudget: working = context_window − max_tokens − вход; блоки = clamp(working×pct, floor, cap); дефолтные ручки: journal 0.08/1500/8000, profile 0.03/800/3000, recall 0.06/1000/6000, log_tail = остаток/6000/40000. working < Σfloor → fail-soft: память выключается, warn. Окно — из models.config.context_window через AIConfig. Не путать с `journal/budget.py` — тот про вытеснение из журнала.

## Write path (write_pipeline.py)

1) `TurnRepository.append_turn` — идемпотентно (ON CONFLICT (user_id, request_id) DO NOTHING); 2) Observer.maybe_run — non-blocking try_hold (занято → молча выйти); 3) sleep-таймер touch. Сбои фона не влияют на записанный ход.

## Хранилища

- **turns/** — таблица core.turns: `Turn`, `TurnRepository`, `render_turn_for_reader` (общий рендер хода для reader-тула и веб-фасада), `load_log_tail` (хвост ленты по токен-бюджету). Термин «лог» доменный: он же в `MemoryContext.log_tail` и настройках `ctx_log_tail_*`.
- **notes/** — core.notes + core.note_entities: `Note`/`NoteDraft`/`NoteKind`, `NoteRepository`, `columns.py` (SQL-словарь колонок, публичный контракт для recall и web_facade), `row_mapping.py` (`row_to_note(row, prefix="")` — единственный конструктор Note из строки, prefix обслуживает self-join почти-дублей), `write_service.py`, `profile_budget.py`.
- **entities/** — core.entities + core.entity_aliases: `Entity`, `EntityRepository` (resolve_names, create_entity, match_in_text). Отдельный домен: заметок не трогает, потребители сквозные (observer, recall, sleep_time, agent_tools).
- **measurements/** — числовые ряды (`mem:memory_measurements`).
- **operation_log.py** — `MemoryOperation`, `MemoryOperationLogRepository`: каждая операция пайплайнов в core.memory_ops, в транзакции вызывающего.
- **watermarks.py** — позиции пайплайнов, `advance` двигает только вперёд (GREATEST).

## Фоновые пайплайны

- **observer/** — триггер: сумма токенов необработанных ходов ≥ порога. Прогон: ходы после watermark (max 30) + known_entities + хвост журнала → один structured LLM-вызов (observations + candidates) → Reconciler → **одна транзакция** (notes + ops + watermark GREATEST) → пост-фаза `apply_journal_budget`.
- **reconciler/** — батч-LLM решает ADD/SUPERSEDE/NOOP/CONTRADICT; **LLM-сбой → fail-open: всё в ADD** (дубли терпимы, потерять хуже).
- **journal/budget.py** — `apply_journal_budget`: журнал за бюджетом → Reflector, затем FIFO-страховка (вытеснение low-weight первыми) в своей короткой транзакции. Вызывается Observer'ом после коммита, под его guard'ом. Симметрия с `notes/profile_budget.py`; лежит отдельно, потому что тянет reflector и recall.
- **reflector/** — свод строк журнала в reflections: precompute (LLM+embeddings) вне транзакции → короткая apply-транзакция. Сбой → False, вызывающий страхуется FIFO.
- **sleep_time/** — SleepTimeScheduler: per-user idle-таймер (~30 мин тишины, touch сбрасывает), blocking hold. `service.py` гоняет задачи в фикс-порядке: entity_cards → period_summaries → duplicate_merge (cosine ≥0.92, ревалидация перед supersede) → probes (вопрос с известным ответом → боевой recall → hit/rank). Каждая задача — свой подпакет с `service.py`/`prompts.py`/`schemas.py`, общее — только `context.py` (SleepContext + адаптеры invoke_structured/try_embed/derive_span). Кросс-импортов между задачами нет. Каждая задача fail-soft. `scheduler.stop()` — async: отменяет таймеры и дожидается их.

## Инварианты (держать при любых правках)

- **LLM строго ВНЕ транзакций; персист атомарен** (одна короткая транзакция на решения + ops + watermark).
- Watermark двигается только после успешного persist, UPSERT с GREATEST (не откатывается).
- Span-провенанс: каждая заметка несёт source_turn_start/end; свёртки наследуют min/max.
- Инвариант subject (preference→user, rule→agent) — на границе вставки NoteRepository.
- profile_budget: переполнение секции профиля → демоция наименее используемых (use_count asc), в одной транзакции с операцией.

## Тулы агента (agent_tools/)

`registry.py` — схемы аргументов и декларативная сборка (`build_memory_tools(runtime, user_id)` собирает StructuredTool поверх хендлеров); `handlers.py` — `MemoryToolHandlers`, замкнут на runtime + user_id. Единственный внешний потребитель — `graph/runtime.py`.

memory_search(query, kinds?, subjects?, limit?), memory_save(content, kind∈{fact,preference,rule}, subject, pin?), memory_revise(statement_to_replace, corrected_statement) — резолв похожей заметки → supersede через write_service (наследует kind/subject/in_journal/journal_weight/pin/теги — «правка не меняет место знания»), memory_read_log(from_turn, to_turn) — дословная сцена из лога; memory_track/memory_stats — measurements-тракт. MEMORY_TOOL_NAMES — top_level_only (субагентам не выдаются). Имена тулов — wire-уровень, не переименовывать.

## Веб-фасад (web_facade/)

Доменная часть фасада для web UI, **без единого импорта fastapi**: contracts (NoteView и прочие вью + `note_view`), `queries.py` (list/search notes, context, overview, entities, turns, ops, `notes_with_refs`, `note_view_by_id`), `operations.py` (create/update/delete/revise), `errors.py` (`MemoryFacadeError` наследует `MemoryDomainError` — дерево ошибок модуля одно). Сами HTTP-роуты и guard self_or_admin — в `app/routes/memory/` (`mem:modules/app`); ручные правки логируются pipeline='ui'.

## Служебное

- llm.py — `invoke_structured(llm_config, schema, messages, *, user_id, task)`: единый structured-вызов всех пайплайнов, fail-soft → None.
- embeddings.py — MemoryEmbedder поверх ai/embeddings: MRL-усечение до 1024 (Qwen3-Embedding) + L2-нормировка после усечения (иначе cosine некорректен); `try_embed`/`try_embed_documents` — «векторизуй или None». Эмбеддер — прямой lookup модели, не ModelRegistry.resolve.
- locks.py — per-user asyncio.Lock: try_hold (observer, неблокирующий) / hold (sleep, ждёт).
- db.py — свой пул asyncpg, транзакции через contextmanager → TransactionExecutor на одном соединении; init-callback регистрирует pgvector-кодек; search_path=core,public. Пул не поднят → `MemoryDatabaseUnavailableError` (транспорт маппит в 503).
- tracing.py — `llm_run_config()` с Langfuse handler: LLM-вызовы пайплайнов видны как generations под спаном пайплайна.

Не сделано: live-смок Observer/sleep на реальном диалоге; юнит-тестов у `entities/` нет (только фейк).
