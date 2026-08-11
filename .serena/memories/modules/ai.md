# Модуль ai/ — фабрики LLM и эмбеддингов

`core/src/bestfiend/ai/`. Строит нативные LangChain-объекты из config-dict (models.config из БД). Своих обёрток над пайплайном нет — максимум нативного стека.

## AIConfig (ai/config.py)

Passthrough `dict[str, Any]` + typed properties: provider, model, api_key, api_base, max_tokens, timeout_s, context_window. `as_kwargs()` отдаёт kwargs для конструктора модели, исключая client-поля и call-time-поля. context_window используется memory/budget для раскладки окна.

## build_chat_model (ai/llm/factory.py)

Диспетчер по provider:
- `openrouter` → ChatOpenRouter (langchain-openrouter);
- `ollama` → ChatOllamaWithExtraSampling (ai/llm/ollama.py);
- `openai` / `groq` → init_chat_model;
- `llamacpp` → init_chat_model с provider="openai" (OpenAI-compatible).

Все модели: `max_retries=0` — ретраи живут на langgraph RETRY_POLICY (react-нода), не в клиенте.

## Провайдер-гочи (закреплены в factory, не удалять при рефакторинге)

- **OpenRouter timeout — в МИЛЛИСЕКУНДАХ** (cfg.timeout_s*1000). `app_url`/`app_title=None` обязательны: иначе SDK создаёт кастомный httpx.AsyncClient и timeout не применяется (вечное зависание).
- **OpenRouter extra_body не существует** (строгая сигнатура send_async): known-ключи (reasoning, plugins, provider-routing) мапятся в нативные поля, остаток — warning + drop. Выключение thinking: `{"enabled": false}` нормализуется в `{"effort": "none"}`.
- **ChatOllamaWithExtraSampling**: добавляет presence_penalty/min_p в options — langchain-ollama 1.1.0 этих полей не знает. Таймаут ollama требует client_kwargs (отложено, не сделано).

## build_embeddings (ai/embeddings/factory.py)

- `ollama` → OllamaEmbeddings (фильтр kwargs по model_fields);
- остальное → OpenAIEmbeddings (OpenAI-compatible; для OpenRouter + 2 совместимость-флага).

⚠️ Эмбеддер для памяти резолвится прямым lookup модели по id, НЕ через ModelRegistry.resolve — per-user подмена модели сломала бы векторное пространство RAG (замешивание эмбеддингов разных моделей).