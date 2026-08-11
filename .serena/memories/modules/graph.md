# Модуль graph/ — LangGraph-агент

`core/src/bestfiend/graph/`. Рекурсивный react-агент, единая модель и единая инструкция на весь граф.

## Диалект промптов — Qwen-native (рефакторинг 2026-07, skill qwen-native-agents)

System-промпт react (`nodes/react/prompts.py`) — структура qwen-code: identity-строка → `# Core Mandates` → `# Primary Workflows` → `# Operational Guidelines` → `# Final Reminder`; язык — английский. Контекст-суффикс (user_instruction `# User Instructions`, capability_overview `# Tool Servers`, memory_stable) приклеен через разделитель `\n\n---\n\n` — как qwen-code клеит память. Волатильный runtime-контекст (environment + recall) оборачивается в `<system-reminder>...</system-reminder>` при вклейке в Human (родной тег Qwen — модель не принимает блок за слова юзера); SUMMARIZE_NUDGE тоже в этом теге. FINALIZE_RULES/KIND_HINTS error-ноды — английские. Описания тулов: жёсткие ограничения капсом (IMPORTANT/DO NOT/ONLY), «когда НЕ использовать» прописано, few-shot в описаниях — теги `<good-example>`/`<bad-example>` (memory_track.metric). Имена тулов не переименовывались осознанно: wire в персистированных tool_calls + прецедент todo_write в qwen-code. При правках промптов держать этот диалект, не инвертировать привычки qwen (краткость, GFM-markdown, no comments).

## Топология (graph/graph.py, graph/nodes/)

`init → react ⇄ tools`, `error` — терминальный сток.
- **init** — рендерит RenderedPrompts (environment, capability_overview, user_instruction, memory_stable, memory_recall) свежими каждый turn; memory_stable = профиль+журнал (рендер заинлайнен в node.py). Guard ZoneInfo в prompts/environment.py: кривая timezone юзера не валит ноду — время рендерится в UTC с лейблом «Unknown (time shown in UTC)».
- **react** — LLM с bind_tools: plain text → результат; tool_calls → tools; soft-gate (remaining_steps ≤ ctx.soft_gate_limit): subagent → summarize (один вызов), top-level → error (loop_exhausted). Обрыв стрима после видимых дельт → ANSWER_RESET перед re-raise (retry не дублирует текст, error-нода не дописывает к обрубку); reset шлётся только при живых дельтах после последнего reset.
- **tools** — исполняет батч tool_calls; для серверов из serial_tool_servers — семафор=1 per connection_id; резолвит внутренние тулы delegate_subtask и send_artifact_to_user. Пакет `nodes/tools/` расслоён: `node.py` — диспетчер, `delegation.py` — запуск дочернего графа, `artifacts.py` — сбор и резолв артефактов. Routing-only тулы (delegate_subtask, send_artifact_to_user) объявляют coroutine-заглушку из `nodes/react/routing_only_tool.py` — прямой вызов бросает RoutingOnlyToolInvokedError.
- **error** — graceful finalize: context_exceeded/loop_exhausted → LLM-вызов по накопленному + KIND_HINTS; provider_down/unexpected → статичный текст.

error_handler (to_error) навешан на init/react/tools; на error-ноде его НЕТ (иначе рекурсия). RETRY_POLICY только на react и только транзиентное (сеть/429/5xx); классификация ошибок — graph/errors.py (context_exceeded по status 400 + паттернам, provider_down по сети/статусам/именам исключений).

## Состояние (graph/state.py — OrchestrationState)

Ключевое: `input: InputContext` (frozen: message, request_id, attached_artifacts, user_environment, user_instruction, journal, profile, recall, tool_catalog: list[ToolServerEntryView]); `stm: list[BaseMessage]` (лента top-level, history+текущий Human); `turn_start_index` (маркер начала turn'а для persist-среза, runtime кладёт len(stm)-1); `processing_mode: task|subagent` (ось dialog снесена 2026-07); `work_history` (лента субагента — subagent пишет сюда, top-level в stm); `created_artifacts` (reducer merge_artifacts, дедуп по artifact_id first-wins); `presented_artifacts` (выбранное send_artifact_to_user → AnswerFinal.attachments); `result`; `error_signal`; `remaining_steps` (managed).

## GraphContext (graph/context.py, frozen)

`model: BaseChatModel` (одна на весь граф), `tools_by_name` (MCP+memory, namespaced), `top_level_only_tool_names` (memory-тулы — субагентам не биндятся), `serial_tool_servers` (namespaced tool → connection_id), `graph` (self-ссылка на скомпилированный граф для delegate_subtask), лимиты `soft_gate_limit` / `max_recursion_depth` / `child_recursion_limit` (дефолты — константы `*_DEFAULT` в graph/config.py, боевые значения заполняет GraphRuntime из GraphSettings).

## Рекурсия delegate_subtask

Дочерний invoke того же графа: processing_mode="subagent", recursion_depth+1 (максимум ctx.max_recursion_depth, дефолт 2), recursion_limit = ctx.child_recursion_limit (дефолт 25). Субагенту не рендерятся memory/environment/recall. Результат → ToolMessage (текст + MD-список artifact_llm_name); артефакты ребёнка мержатся в created_artifacts родителя. Субагент НЕ стримит.

## Стриминг (stream_keys.py + streaming.py)

`invoke_graph(state, event, ctx, *, graph, publisher, recursion_limit, langfuse_handler_provider)` — astream(stream_mode=["custom","values"]); langfuse-провайдер резолвится ВНУТРИ защищённой зоны (кидающий провайдер не оставит подписку без AnswerFinal/close). Custom-ключи: `answer_delta` (видимый текст), `progress_step` («вызываю {tool}»), `answer_reset`. **Preface-логика**: react оптимистично стримит content как дельты; первый tool_call_chunk означает «это был preface, не ответ» → ProgressStep(preface) + AnswerReset, накопленное забывается. Финальный текст = joined streamed chunks || state.result || статик. `publish_final(publisher, request_id, text, attachments)` — единая точка AnswerFinal.

## GraphRuntime (graph/runtime.py) — публичный API модуля

`process_input_event(InputEvent)`: параллельно memory.search + MCP discovery → build state/context (resolve модели через ModelRegistry, build_chat_model, memory_tools + build_mcp_tools; лимиты из GraphSettings) → invoke_graph → AnswerFinal (+attachments из presented_artifacts) → фоновый persist_turn. Все detached-задачи (persist, use_count bump в search) уходят в BackgroundTaskSupervisor — он передаётся в ctor GraphRuntime, владелец — CoreRuntime.

**Persist (graph/persist.py, persist_turn)**: turn = [HumanMessage] + react_loop (без финального AI) + AIMessage(answer_text — доставленный текст); `_sanitize_react_loop` вырезает осиротевшие AI(tool_calls) в хвосте — иначе отравят будущие загрузки истории. System-prompt НЕ персистится (prepend на каждом invoke).

## Приложенные файлы (attached_artifacts.py) + нативные картинки

`enrich_human_with_artifacts` — MD-блок «Приложенные файлы» в HumanMessage + структурные рефы в additional_kwargs["attached_artifacts"] (whitelist полей: artifact_id, artifact_user_name, type, description). storage_key НЕ персистится — резолвер (`ArtifactService.read_bytes_for_user(user_id, artifact_id)`) строит ключ из session user_id, хранёному не доверяет.

**Vision-гидрация (invoke-time, STM/PG не трогает).** `hydrate_image_artifacts`: Human'ы с refs type=image → content=[text-блок, v1 image-блоки {type:image, base64, mime_type}]. Текущий ход (последний Human) — целиком; история — от хвоста ≤ vision_max_history_images (6) картинок, граничное сообщение частично; fail-soft per-артефакт (oversize > vision_max_image_bytes (5МБ raw), сбой чтения, не-картиночный ext через `image_mime_type`). `strip_image_blocks` в persist_turn — обратно в str, ТОЛЬКО HumanMessage (списочный контент AI — провайдерский, 1-в-1); dict-дамп после strip == догидрационному (тест-инвариант). Гейт: `AIConfig.supports_vision` (models.config, в _META_FIELDS — не утекает в kwargs провайдера) → runtime строит pre-bound closure `GraphContext.hydrate_images` (None = vision off), вызов после планирования бюджета памяти (бюджет считается по тексту). react `_prepend_runtime_context` вклеивает волатильный контекст в первый text-блок, картинки целы. `delegate_subtask(task, artifact_llm_names)` — сид work_history ребёнка (enrich+hydrate), ненайденные имена перечисляются в ToolMessage. Общий пул адресуемых по имени артефактов `known_artifacts(state)` = attached + created (дедуп по artifact_id) — един для send_artifact_to_user/_resolve_presented/delegate. Оба адаптера (.venv): langchain-openrouter конвертит v1-блоки в `_format_message_content` (HumanMessage; ToolMessage с блоками НЕнадёжен — строгая pydantic-валидация SDK), langchain-ollama — в `images`. Отложено: downscale, view_artifact-тул, vision-enrichment на ingress, voice/video/PDF.

## Конфиг (graph/config.py)

ModelIDSettings.model_id (env MODEL_ID, дефолт "orchestrator-default" — id строки в core.models). GraphSettings: `graph_recursion_limit` (GRAPH_RECURSION_LIMIT, 100), `child_recursion_limit` (GRAPH_CHILD_RECURSION_LIMIT, 25), `soft_gate_limit` (GRAPH_SOFT_GATE_LIMIT, 3), `max_recursion_depth` (GRAPH_MAX_RECURSION_DEPTH, 2), `auto_artifact_token_threshold` (20000). Дефолты лимитов — константы `*_DEFAULT` там же (единый источник и для полей GraphContext).

Трейсинг: Langfuse CallbackHandler через lazy provider на invoke; request_id = uuid7 на ingress.