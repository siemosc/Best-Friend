# Definition of Done — что гонять после правок

## Python (core)

Из корня репо, порядок:

```bash
# 1. Lint + формат (авто-фикс)
PYTHONIOENCODING=utf-8 uv run --project tools/qa python tools/qa/dev_code_fix.py --service core lint

# 2. Типы (pyright)
PYTHONIOENCODING=utf-8 uv run --project tools/qa python tools/qa/dev_code_fix.py --service core types

# 3. Тесты
uv run --directory core pytest
```

⚠️ Всегда явно `--service core` — автодетект по diff пути core/ не мапит. `PYTHONIOENCODING=utf-8` обязателен на Windows. После массовых правок импортов — сперва `uv run --directory core ruff check --fix` (раннер не автофиксит I001).

Fallback без QA-venv: `uv run --directory core ruff check --fix` + `uv run --directory core ruff format` (ruff-конфиг подтянется из корневого pyproject).

Полный прогон (все сервисы, все проверки): `... dev_code_fix.py --all`.

## Web

```bash
cd web && pnpm check    # svelte-check + tsc
```

## Не забыть

- Правки схемы БД — только новой миграцией `core/scripts/migrations/NNN_*.sql` (следующий номер), применится сама на старте core.
- Тесты для нового кода кладутся в зеркальный путь `core/tests/<module>/`.