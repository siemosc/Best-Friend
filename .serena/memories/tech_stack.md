# Tech Stack

## Python (core)

- Python **>=3.11,<3.12** — строгий пин, 3.12 не допускается.
- Пакетный менеджер — **uv**, per-service: у `core/` и `tools/qa/` свои `.venv` + `uv.lock`. Корневой pyproject — только общий ruff-конфиг, без зависимостей.
- Сборка core: hatchling, пакет `core/src/bestfiend`.

Ключевые зависимости core:
- FastAPI + uvicorn (HTTP), pydantic v2 + pydantic-settings. SSE-стека нет — sse-starlette снесена вместе с SSE-входом (v29.9.0).
- **LangGraph >=1.0.7 + LangChain >=1.3.1 (нативный стек)**: langchain-openai, langchain-groq, langchain-ollama, langchain-openrouter. Кастомных обёрток над LLM-пайплайном нет — всё нативное.
- aiogram 3 (Telegram), telegramify-markdown.
- asyncpg + pgvector (PostgreSQL, схема `core`), boto3 (S3/SeaweedFS для артефактов).
- fastmcp 3.2.x (MCP-клиент), langfuse v4 (observability), loguru, orjson, tiktoken, uuid6.
- Тесты: pytest + pytest-asyncio, `asyncio_mode = "strict"` (маркировать async-тесты явно).

⚠️ LangChain/LangGraph/Langfuse меняются быстро — API сверять по `.venv` установленной версии или Context7, не по памяти модели.

## Web

- SvelteKit 2 + **Svelte 5** (runes), Vite 8, TypeScript 6, Tailwind CSS 3.
- Пакетный менеджер — **pnpm** (pnpm-lock.yaml).
- adapter-static → SPA, dev-сервер vite на 5173.

## Инфра

- PostgreSQL + SeaweedFS — обязательная инфра, дефолт `docker compose up -d`; core зависит от обоих.
- Langfuse — внешний бэкенд (cloud или свой инстанс); self-hosted стек удалён из репо в v29.14.0.

## QA-тулинг (`tools/qa/.venv`)

ruff (единый конфиг в корневом pyproject), pyright (ad-hoc pyrightconfig per-target), bandit, vulture, radon. Раннер: `tools/qa/dev_code_fix.py` — команды в `mem:suggested_commands`.