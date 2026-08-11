# Архитектура артефактов — согласованные решения

> **ИСТОРИЧЕСКОЕ (последняя правка 2026-05-16, микросервисная эпоха).** Документ описывает снесённые части: PG-registry артефактов (заменён на meta.json в SeaweedFS), регистрацию в ContextForge (CF удалён v29.0.3), ноду `final_answer` (удалена пивотом графа на рекурсивный react). Актуальное состояние — Serena memories `modules/artifacts` и `architecture`. Оставлен как история решений по модели ArtifactRef/art_meta.

## Цель

Файл = артефакт. Главная цель — корректная работа с контекстным окном: документы на 5к строк не должны лежать в контексте LLM, они должны храниться отдельно и предоставляться по требованию.

Артефакты появляются из двух источников:
- **user** — пользователь загружает файлы в диалог;
- **agent** — сервисы и MCP-инструменты создают артефакты в ходе работы.

## Часть 1. Целевая архитектура

### Сущности

**ArtifactRef** (в `bestfiend_shared`) — нейтральный дескриптор артефакта для передачи по системе.

Поля делятся по двум осям: **(1) LLM-видимое vs системное** и **(2) унифицированное vs type-specific**. Из четырёх комбинаций используются три категории хранения:

| Категория | Поля | Контракт |
|---|---|---|
| **Плоско, LLM-видимое** | `artifact_id`, `type`, `semantic_name`, `description` | Обязательное, типизированное, попадает в LLM-контекст |
| **Плоско, системное** | `path` | Обязательное, типизированное, скрыто от LLM хелпером `dump_artifact_for_llm` |
| **`art_meta: dict[str, Any]`** | type-specific (image dims, doc pages, audio duration) | Опционально, динамика, скрыто от LLM по соглашению |

- `artifact_id: str` — уникальный ID вида `{semantic_name}_{uuid7_tail}`.
- `type: str` — категория (image, document, table, code, archive, audio, video, binary, other).
- `semantic_name: str` — slug в kebab-case.
- `description: str` — что внутри, откуда, как описать. **Canonical место для LLM-релевантной информации**: если type-specific детали из `art_meta` должны быть видны агенту (например, разрешение картинки) — переплавляем их в текст description, не показываем art_meta напрямую.
- `path: str` — opaque locator, не consumer-readable. Унифицированный системный инвариант: каждый артефакт его имеет, но LLM не должна на него смотреть.
- `art_meta: dict[str, Any]` — свободная структура для type-specific параметров. Кто пишет и кто читает — забота конкретных type-handler'ов в artifacts service и принимающих MCP-сервисов. LLM в art_meta не лезет.

**Правило записи новых полей:** обязательное + унифицированное → плоско (расширяем exclude в хелпере, если поле системное). Опциональное + type-specific → в `art_meta`. Серых зон по делению нет.

### artifacts сервис

`artifacts` — единая точка владения артефактами. Два входа в один сервис (паттерн notifications), различаются по тому, **кто формирует запрос**:

- **FastAPI** — детерминированные HTTP-ручки для **кода**: любой MCP-сервис, telegram_gateway, auto-offload в OC. Запрос формирует код по известному payload или известному id. Сюда же — create, from-raw, get-bytes.
- **FastMCP** — MCP tool для **LLM-оркестратора**. Единственная операция, где запрос рождает модель из контекста диалога, а не код — поиск по фонду (`artifacts.search`). Регистрируется в ContextForge с тегом `bestfiend-mcp`.

Принцип: code-driven (известен payload/id) → FastAPI. LLM-driven (query из смысла диалога) → FastMCP. Других LLM-инициируемых операций над артефактами нет — оркестратор только менеджер id'шек.

Внутри сервиса один LLM-пайплайн enrichment (`enrichment.py`), который используется всеми путями, не предоставившими готовое описание:
- пользовательский upload без caption;
- агентский raw text без metadata;
- auto-offload fallback из orchestration_core.

### Пути создания артефакта (всё через FastAPI)

Создание всегда инициирует **код**, не LLM. Оркестратор артефакты не создаёт.

| Путь | Кто инициирует | Контракт |
|---|---|---|
| **trusted-create** | код MCP-сервиса с готовой metadata | `POST /internal/artifacts` |
| **enrichment-create** | код MCP-сервиса без metadata | `POST /internal/artifacts/from-raw` |
| **auto-offload** | orchestration_core при превышении лимита токенов | `POST /internal/artifacts/from-raw` |

Любой путь возвращает полный `ArtifactRef` — caller сразу получает метаданные в свой контекст.

### Чтение и поиск

- **Чтение bytes по id** — `GET /internal/artifacts/{id}/bytes`, FastAPI. Зовёт **код** принимающего MCP-сервиса по id, который оркестратор передал ему в task_args. LLM сам bytes не читает.
- **Поиск по фонду** — `artifacts.search(query, type?)`, **MCP tool** (FastMCP). Единственная LLM-инициируемая операция: модель решает «нужен тот отчёт про продажи из прошлого» и формирует query из контекста. Возвращает список `ArtifactRef`. Под tool'ом — детерминированная FastAPI-реализация `/internal/artifacts/search` (паттерн notifications: один процесс, два входа, общая логика).

### Источники и поток через стейт

#### User upload

```
telegram_gateway:
  parse attachment → upload в artifacts HTTP → ArtifactRef
                   → InputEvent.attached_artifacts: list[ArtifactRef]
↓
orchestration_core ingress:
  InputEvent → InputContext.attached_artifacts (внутри OrchestrationState.input, frozen)
↓
init нода:
  передаёт current user_text + attached_artifacts в memory_service.load(...)
↓
memory_service:
  собирает messages с уже встроенным md-блоком артефактов внутри user message
↓
LLM получает готовые messages, артефакт виден как часть запроса пользователя
```

`telegram_gateway` — единственная точка инжекта пользовательских артефактов. Bytes за пределы edge не таскаются; в ingress и дальше идёт только `ArtifactRef`.

#### Agent output

```
MCP-сервис (его код, не LLM):
  POST /internal/artifacts | /from-raw  (FastAPI)
  → возвращает ArtifactRef, сервис кладёт его в свой MCP tool result

orchestration_core execute_agent:
  результат → ExecutionResultState.artifacts: list[ArtifactRef]
            → execution_history (через append_execution_history reducer)

orchestration_core auto_offload (fallback):
  если result превысил token threshold → HTTP /from-raw
  → result_summary заменяет content, artifacts расширяет список

supervisor → relevant_task_ids:
  отбирает только успешные/нужные задачи для финиша

final_answer:
  render_final_results использует relevant_task_ids
  → markdown с artifact_ids от relevant задач уходит как assistant_message
```

### Передача артефактов между агентами

Только через `arguments` MCP tool. Конкретное имя поля и форма (`input_artifacts: list[str]`, либо встроено в основное поле tool'а) — забота схемы конкретного tool, не общий контракт.

`X-BF-Meta` для артефактов **не используется**: LLM не видит meta, значит планировщик/supervisor не может туда писать.

### LLM-видимый формат артефактов в OC

Артефакты, попавшие в `ExecutionResultState.artifacts`, рендерятся для LLM в двух местах через единый хелпер `dump_artifact_for_llm` ([orchestration_core/src/graph/nodes/shared/artifact_render.py](../services/orchestration_core/src/graph/nodes/shared/artifact_render.py)). Хелпер исключает `path` (opaque locator) и `art_meta` (type-specific детали) — LLM видит только то, чем может оперировать: `artifact_id`, `type`, `semantic_name`, `description`.

**Action_react loop** ([tool_executor.py](../services/orchestration_core/src/graph/nodes/action_react/tool_executor.py)): результат каждого MCP tool call оборачивается в JSON-payload `{"content": ..., "artifacts": [...]}`. Агент видит свои артефакты, созданные в прошлых шагах своего же loop'а. Формат единый для всех tool calls — `artifacts` может быть пустым массивом.

**Supervisor (work_history)** ([execution/threading.py](../services/orchestration_core/src/graph/nodes/execution/threading.py)): тот же payload-формат в delta `assistant(tool_call) + tool(result)` per execute. Supervisor видит артефакты всех тасок и может ссылаться на их `artifact_id` в task_args следующих тасок.

`TaskPlanView` (planner) — без артефактов. У planner нет runtime истории, артефакты ему не нужны.

`parse_artifacts_from_raw` в том же модуле — единая точка валидации `list[dict]` от MCP в `list[ArtifactRef]` (используется и в `execution/node.py`, и в `tool_executor.py`).

### Cross-turn видимость

Артефакт `permanent` в PG, но контекстное окно один turn. Три слоя видимости:

| Слой | Кто отвечает | Что видно |
|---|---|---|
| **PG registry** | artifacts сервис | source of truth, аудит для системы |
| **Runtime context** | OrchestrationState + memory | артефакты текущего и прошлых turn'ов через STM |
| **Глобальный поиск** | `artifacts.search` MCP tool | grep по всему фонду пользователя |

LLM-агент видит артефакты в runtime через текст messages, который рендерит memory_service. Каталог-блок отдельным сообщением в init **не делается** — лишний канал.

### Память и артефакты

Memory_service владеет рендером `attached_artifacts` в user_message. Использует один и тот же render helper в двух точках: read path (для текущего turn'а на load) и write path (для сохранения rendered текста в БД).

**Read path** (`memory.search`, начало графа):
- принимает `current_user_text` + `current_attached_artifacts: list[ArtifactRef]`.
- Внутри клеит attached_artifacts в md-блок внутри user_message текущего turn'а.
- Возвращает messages, включая прошлые пары из STM (которые уже содержат встроенные artifacts из своих write phase) и текущий user_message с актуальным блоком.

**Write path** (после `final_answer` → `memory.write_pair`):
- принимает **raw** `user_text`, `assistant_text`, `attached_artifacts: list[ArtifactRef]`, `created_artifacts: list[ArtifactRef]`.
- Внутри тот же render helper применяется к (user_text, attached_artifacts) → сохраняется rendered user_text в БД.
- `assistant_text` сохраняется как есть — `created_artifacts` уже упомянуты по `artifact_id` в его тексте через `final_answer` markdown.
- Дополнительно сохраняются `attached_artifacts` и `created_artifacts` как JSONB-колонки рядом с парой — informational «в этом шаге были такие артефакты», для будущих use-cases (FK-проверки, search по фонду).
- `created_artifacts` фильтруются orchestration_core по `relevant_task_ids` перед передачей — клеятся только артефакты от удачных/нужных задач.

**На следующих turn'ах:**
- Memory отдаёт прошлые пары из БД как есть. User_text уже содержит встроенные `attached_artifacts` (сохранён так в write phase). Assistant_text упоминает `created_artifacts` по `artifact_id` через final_answer markdown. Никакого дополнительного рендера для прошлых пар.

Различие `attached` vs `created` хранится явно — это разный источник, разная семантика. Конкретный layout md-блока (структура, поля, позиция) — обсуждается на реализации.

### Что НЕ входит

- **Multimodal** (image как vision-input в LLM call) — фича на развитие.
- **Удаление артефактов пользователем** — отдельная история, не в этой архитектуре.
- **Версионирование артефактов** — не предусмотрено, артефакт immutable после `active`.
- **Память как артефакт** — память отдельная сущность со своим MCP tool, артефактом не считается.
- **Каталог артефактов в промпте** — отдельным system block не делаем; артефакт виден только если попал в runtime через memory или поиск.

### TODO

- **Рендер `created_artifacts` в historical STM — СДЕЛАНО (Phase 3, v27.1.0).** memory встраивает created в `work_digest` текст при `save_pair` (заголовок «Созданы артефакты:»), симметрично attached в user_message. JSONB-колонка остаётся для трассировки. Edge case `work_digest=None`+created → блок становится work_digest. Подробности — раздел «Память и артефакты».
- **Открытые направления развития:** фильтр created по релевантности к текущему сообщению (сейчас рендерятся все relevant по `relevant_task_ids`); поиск по `created_artifacts` — закрывается Phase 4 (`artifacts.search`).

## Часть 2. Анализ — текущее состояние vs целевое

### Уже есть

- `ArtifactRef` контракт в `bestfiend_shared.artifacts` с `art_meta: dict[str, Any]` (Phase 1).
- `artifacts` сервис: trusted-create (`POST /internal/artifacts`), enrichment+bytes (`POST /internal/artifacts/from-raw`), storage (atomic FS), registry (PG, статусы `creating`/`active`/`failed`), отдача bytes (`GET /internal/artifacts/{id}/bytes`).
- `enrichment.py` — единый LLM-пайплайн для генерации metadata (используется в `from-raw` для text path). Для bytes — fallback без LLM (`_infer_artifact_type` по filename, slug semantic_name).
- `auto_offload` в orchestration_core — auto-конвертация больших MCP-результатов в артефакты.
- User upload pipeline (Phase 2): telegram_gateway → artifacts → InputEvent.attached_artifacts → memory render → STM.
- `ExecutionResultState.artifacts: list[ArtifactRef]` в `execution_history` (append-reducer).
- `relevant_task_ids` уже есть: supervisor заполняет через `ReviseAndRunTransition` на `task_finish`, `render_final_results` фильтрует execution_history.
- `artifact_ids` рендерятся в markdown final_answer (`format_final_results_markdown`) и уходят в assistant_message → STM.
- Менеджерский рендер артефактов в OC (см. «LLM-видимый формат артефактов в OC»): хелпер `dump_artifact_for_llm` + `parse_artifacts_from_raw` в `graph/nodes/shared/artifact_render.py`; action_react видит свои артефакты в tool result через JSON-payload; supervisor видит артефакты всех тасок через work_history с полным `ArtifactRef` (минус `path`/`art_meta`).

### Изменить или расширить

#### artifacts сервис

- create / from-raw / get-bytes уже есть как FastAPI — менять не нужно (code-driven).
- Добавить **FastMCP-слой** параллельно с FastAPI (паттерн notifications) с **единственным** tool `artifacts.search` — единственная LLM-инициируемая операция.
- `search` — новый поисковый индекс по metadata (semantic_name, description, type, возможно по контенту) + FastAPI-реализация под MCP-обёрткой.

#### orchestration_core

- `InputContext` (внутри `OrchestrationState.input`, frozen): добавить `attached_artifacts: list[ArtifactRef]`. Reducer не нужен — поле immutable весь turn, пишется только при `_build_state` из `InputEvent.attached_artifacts`.
- `_fetch_memory`: пробрасывать `state.input.attached_artifacts` в `memory.search` как `current_attached_artifacts`.
- После `final_answer` при вызове `memory.write_pair`: передавать `attached_artifacts` (из `state.input.attached_artifacts`) и `created_artifacts` (из `execution_history`, отфильтрованные по `relevant_task_ids` — фильтр живёт в OC, не в memory).

#### memory_service

- Расширить `MemorySearchRequest`: добавить `current_attached_artifacts: list[ArtifactRef]`.
- Внутри search: render helper клеит attached_artifacts в md-блок внутри user_message текущего turn'а.
- Расширить `WritePairRequest`: добавить `attached_artifacts: list[ArtifactRef]`, `created_artifacts: list[ArtifactRef]`.
- Внутри write_pair: тот же render helper применяется к (raw user_text, attached_artifacts) → сохраняется rendered text в БД.
- Хранение: отдельные JSONB-колонки `attached_artifacts` и `created_artifacts` в таблице `stm` (миграция).

#### Транспортные контракты

- `ArtifactRef` (в `bestfiend_shared.artifacts`): добавить `art_meta: dict[str, Any]` со значением по умолчанию `{}`.
- `InputEvent` (в `bestfiend_shared.events`): добавить `attached_artifacts: list[ArtifactRef]`.
- `memory.write_pair` запрос: добавить `attached_artifacts`, `created_artifacts`.
- `memory.search` запрос: добавить `current_attached_artifacts`.

### Делать с нуля

#### telegram_gateway

- Парсинг attachments из aiogram update (photo, document, video, audio).
- Загрузка bytes в `artifacts` HTTP (новый endpoint или существующий `/internal/artifacts` с готовыми metadata от gateway).
- Формирование `InputEvent.attached_artifacts` из полученных `ArtifactRef`.
- Решение: пишет ли gateway свои metadata (file_name, mime), или просто отдаёт bytes и пользуется enrichment-пайплайном.

#### control_plane

- Регистрация `artifacts` MCP gateway в ContextForge при startup (как для notifications). Тег `bestfiend-mcp` для strict v2.

#### artifacts.search инфраструктура

- Поисковый индекс по metadata (либо PG full-text, либо отдельное решение).
- Контракт фильтров (по type, по user — через resolve_meta из X-BF-Meta).

### Out of scope этой итерации

- Multimodal pipeline (image → vision LLM call).
- UI для управления артефактами пользователем (просмотр, удаление).
- Шифрование payload на стороне storage.
- Cross-user shared artifacts.

## Фазы реализации

Четыре фазы. Каждая даёт самостоятельный проверяемый результат. Конкретные шаги, контракты и DoD по фазе — в Plan-фазе при заходе на неё.

### Фаза 1. Контрактные основания — ✅ СДЕЛАНО (v27.0.0)

Расширение shared и стейта без поведения. Изолированно, не ломает существующий flow.

- [ ] `ArtifactRef.art_meta: dict[str, Any] = {}` в `bestfiend_shared.artifacts`
- [ ] `InputEvent.attached_artifacts: list[ArtifactRef] = []` в `bestfiend_shared.events`
- [ ] `InputContext.attached_artifacts: list[ArtifactRef] = []` (внутри `OrchestrationState.input`, frozen, без reducer)
- [ ] Маппинг `event.attached_artifacts` → `InputContext` в `CoreService._build_state`

**Видимый результат:** пользователю ничего; downstream фазы опираются на готовые контракты.

### Фаза 2. User upload pipeline — ✅ СДЕЛАНО (v27.0.0)

Главная пользовательская ценность: пришёл файл — бот видит его в контексте.

- [ ] `artifacts`: переименовать endpoint `/from-raw-text` → `/from-raw`, расширить контракт (XOR `payload_bytes`/`payload_text` + optional `filename`)
- [ ] `artifacts`: переименовать `agent_id` → `art_source` в request-контрактах и `ArtifactRecord` (миграция колонки), нормализация значения при формировании storage path
- [ ] `telegram_gateway`: handler attachments + буферизация media groups по `media_group_id` (debounce ~1-2 сек) + параллельная загрузка через `asyncio.gather` в `/from-raw` → `InputEvent.attached_artifacts`
- [ ] `memory_service.search`: новое поле `current_attached_artifacts`, render helper клеит md-блок в user_message текущего turn'а
- [ ] `memory_service.write_pair`: новые поля `attached_artifacts`, `created_artifacts`; внутри тот же render helper применяется к raw user_text → сохраняется rendered text в БД; refs сохраняются в JSONB-колонках для трассировки
- [ ] `memory_service`: миграция — JSONB-колонки `attached_artifacts`, `created_artifacts` в таблице `stm`
- [ ] `orchestration_core._fetch_memory`: пробрасывает `state.input.attached_artifacts` в `memory.search`
- [ ] `orchestration_core` after final_answer: вызов `memory.write_pair` с attached + filtered created (по `relevant_task_ids`)

**Видимый результат:** «пришли .md/photo → бот видит их в текущем turn'е и помнит на следующих».

### Фаза 3. Cross-turn видимость agent-created — ✅ СДЕЛАНО (v27.1.0)

Замыкаем цикл: то, что агенты создают, живёт в STM как первоклассная сущность, фильтруется по `relevant_task_ids`.

- [ ] `orchestration_core` после `final_answer`: формирует `created_artifacts` из `execution_history` по `relevant_task_ids`, передаёт в `memory.write_pair`
- [ ] `memory_service.write_pair`: новое поле `created_artifacts`, хранение раздельно от attached
- [ ] `memory_service.load`: рендер created в md (формат отдельно от attached, см. I1 в архитектуре)

**Видимый результат:** «агент собрал отчёт в turn 1 — юзер ссылается на него в turn 5 — бот находит в STM».

### Фаза 4. Поиск по фонду (artifacts.search) — ⏳ НЕ НАЧАТА

Единственная LLM-инициируемая операция над артефактами. create / from-raw / get-bytes остаются code-driven через FastAPI — в MCP **не выносятся**. auto-offload не трогаем.

- [ ] `artifacts` сервис: FastMCP-слой параллельно с FastAPI (паттерн notifications) с **единственным** tool `artifacts.search(query, type?)`
- [ ] Поисковый индекс по metadata (semantic_name + description + type; pgvector vs PG full-text — open question) + FastAPI-реализация `/internal/artifacts/search` под MCP-обёрткой
- [ ] `control_plane`: регистрация `artifacts` MCP gateway в CF (тег `bestfiend-mcp`)
- [ ] Фильтр по user — через resolve_meta из X-BF-Meta

**Видимый результат:** агент по смыслу диалога ищет среди прошлых артефактов пользователя, получает их `ArtifactRef`, дальше оперирует id (передаёт в task_args, код принимающего сервиса читает bytes).

### Зависимости

```
Фаза 1 (контракты)        ✅ v27.0.0
  ↓
Фаза 2 (user upload)     ✅ v27.0.0  ← независимо от Фаз 3 и 4
  ↓
Фаза 3 (agent-created)   ✅ v27.1.0  ← опиралась на расширение memory из Фазы 2
  ↓
Фаза 4 (только search)   ⏳ не начата ← без неё Фазы 1-3 уже дают value, можно отложить
```
