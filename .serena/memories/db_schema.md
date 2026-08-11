# Схема БД (актуальное состояние после миграций 001–015)

PostgreSQL 16 + pgvector. БД `bestfiend`, наши таблицы — схема **`core`** (приложение ставит `search_path=core,public` через asyncpg server_settings). Схема `contextforge` — пустое наследие CF. Расширения `vector` и `pg_trgm` — в **public** (гоча: search_path миграций = core,public, расширения создавались явно `SCHEMA public`, иначе pgvector-кодек не найдёт тип).

Реестр применённого — `core._migrations` (name). Файлы: `core/scripts/migrations/NNN_*.sql`, применяются на старте core.

## Identity / control plane

- `users` — user_id uuid PK, telegram_chat_id (uniq), discord_user_id, login (uniq partial), password_hash, role user|admin, status pending|active|banned, locale (дефолт ru; «на вырост», читателя в коде нет — не сносить), timezone (дефолт Europe/Belgrade), city, country. cf_* колонки удалены (006).
- `sessions` — session_id PK, user_id FK, expires_at.
- `auth_binding_codes` — 6-значный code PK, user_id (uniq), expires_at — привязка Telegram к аккаунту.
- `models` — id text PK, name, config jsonb (полный конфиг LLM: provider, параметры, context_window, max_tokens). ⚠️ Данные восстановлены руками, снапшот 001 их НЕ содержит — не пересоздавать БД без бэкапа.
- `user_assistant_configs` — user_id PK, user_instruction text, llm_custom_config jsonb (непустой = полная замена дефолт-конфига модели по структуре models.config). Схлопнуто из 4 per-slot инструкций (004).

## MCP (модель «сервер + подписка», 006–008; OAuth — 016)

- `mcp_connections` — connection_id PK, name uniq, url, transport ('http_stream' only), auth_type none|bearer|oauth (CHECK расширен в 016), is_public, is_system, timeout_s, supports_parallel_tool_calls (false → граф сериализует вызовы этого сервера семафором=1).
- `mcp_subscriptions` — (user_id, connection_id) PK, auth_token (персональный bearer), enabled, disabled_tools jsonb, timeout_s nullable (per-user override, COALESCE с connection).
- `mcp_oauth_clients` (016) — OAuth-клиент per connection: connection_id PK=FK CASCADE, client_id, client_secret nullable, token_endpoint_auth_method basic|post|none (без DEFAULT, пишет сервис; CHECK: method≠none ⇒ секрет NOT NULL), source preregistered|dcr, client_secret_expires_at.
- `mcp_oauth_flows` (016) — незавершённые авторизации: state PK, user_id/connection_id FK CASCADE, code_verifier (PKCE), redirect_uri, token_endpoint, issuer (сверка iss из callback), resource (RFC 8707), scope, expires_at (TTL ~10 мин; ленивый purge на start_flow); одноразовость — DELETE..RETURNING.
- `mcp_oauth_tokens` (016) — (user_id, connection_id) PK: access_token, refresh_token nullable, expires_at nullable, scope, token_endpoint (refresh без re-discovery), refresh_failed_at (invalid_grant → UI-статус expired).

## Память (лог-центричная, 009–014; дизайн: docs/memory_architecture_alt.md)

- `turns` — лог, источник истины. Одна строка = один ход: user_message jsonb ([HumanMessage]), react_loop jsonb ([AI(tool_calls), ToolMessage, ...]), ai_message jsonb ([AIMessage]) — сериализация messages_to_dict 1-в-1; token_count_full/token_count_loop (loop — «на вырост», данные копятся, читателя нет — не сносить); UNIQUE (user_id, request_id). Бывшая `stm` (переименована в 010, история: 009 ввела «тупое ядро»).
- `notes` — единственный атом памяти: kind (observation|fact|preference|rule|reflection|entity_card|period_summary), subject (user|agent|world, NULL для производных), content, bitemporal (event_time nullable / observed_at), status (active|superseded|contradicted) + superseded_by, pinned + pin_section (identity|preferences|relationships|rules) → профиль, in_journal + journal_weight (2/1/0) → журнал, source_turn_start/end (провенанс span'а лога), embedding vector(1024), content_tsv (russian FTS, generated), use_count, pipeline_ver. Vector-индекса нет сознательно — exact scan.
- `entities` — реестр сущностей: canonical_name (uniq per user по lower), embedding vector(1024). `entity_aliases` — алиасы + gin_trgm индекс (толерантный матч). `note_entities` — теги M:N.
- `memory_watermarks` — (user_id, pipeline) → last_turn_id: идемпотентность фоновых пайплайнов.
- `memory_ops` — ops-лог «почему запомнил/забыл»: pipeline (observer|reconciler|reflector|tool|sleep|ui), op (add|supersede|noop|contradict|evict|reflect|pin|unpin|demote|revise|merge|delete|edit), note_id/target_note_id (SET NULL — лог переживает заметку), detail.
- `memory_probes` — автопробы качества recall: question, expected_note_id, hit, rank (hit@k).
- `measurements` (015) — числовые ряды жизни: metric+value (вес, сон) и события-счётчики (value NULL); bitemporal, tags jsonb, source. Тракт целиком: `mem:memory_measurements`.

Инвариант subject: preference→user, rule→agent — держится кодом на границе вставки (NoteRepository), не в БД.

## Конвенции миграций

- Plain SQL, инкрементальные, forward-only, строго аддитивные где возможно; каждый файл начинается комментом-обоснованием.
- В шапке новых миграций принято проговаривать, что core.models не затрагивается.
- Снесённые сущности: artifacts_registry (005, метаданные → S3 meta.json), 3 поколения MCP-storage + cf-колонки (006), stm парная модель (009/010).