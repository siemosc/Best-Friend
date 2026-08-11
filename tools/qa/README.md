# tools/qa — общий QA-инструментарий монорепо

Изолированный `uv`-проект, который держит инструменты статического анализа для всех целей BestFiend в одном venv. В нём нет ни одной prod-зависимости — только `ruff`, `pyright`, `bandit`, `vulture`, `radon`.

## Зачем

- **ruff / bandit / vulture / radon** запускаются из этого venv и не требуют зависимостей сервисов.
- **pyright** запускается тоже отсюда, но через сгенерированный `pyrightconfig.json` указывает на `.venv` нужного сервиса — типы видит в реальном окружении.
- **pytest** — не наша зона. Тесты остаются per-service и запускаются командой `dev_code_fix.py test`, которая делегирует в `uv run --directory <service> pytest`.

## Установка

```bash
uv sync --project tools/qa
```

## Использование

Главная точка входа — `tools/qa/dev_code_fix.py`. По умолчанию работает по `--changed` (только цели, которых коснулся `git diff`).

```bash
# Только изменённые цели — lint + types + imports
uv run --project tools/qa python tools/qa/dev_code_fix.py

# Всё подряд
uv run --project tools/qa python tools/qa/dev_code_fix.py --all

# Одна цель, конкретный набор проверок
uv run --project tools/qa python tools/qa/dev_code_fix.py --service core lint
uv run --project tools/qa python tools/qa/dev_code_fix.py --service core types

# Полный набор: lint + types + imports + sec + vulture + radon
uv run --project tools/qa python tools/qa/dev_code_fix.py --all all

# Тесты (тонкая обёртка над per-service pytest)
uv run --project tools/qa python tools/qa/dev_code_fix.py --all test
uv run --project tools/qa python tools/qa/dev_code_fix.py --service core test

# Отчёт сохранить в произвольный путь
uv run --project tools/qa python tools/qa/dev_code_fix.py --all --report-json report.json
```

По умолчанию агрегированный JSON-отчёт пишется в `tools/qa/.cache/report.json`.

## Что покрывается

| Цель                | lint | types | sec | vulture | radon | imports | test |
| ------------------- | :--: | :---: | :-: | :-----: | :---: | :-----: | :--: |
| `core`              |  ✅  |  ✅   | ✅  |   ✅    |  ✅   |   ✅    |  ✅  |

## Pre-commit

Локальный pre-commit хук `dev-code-fix` запускает раннер с `--from-files` и `pass_filenames: true` — то есть проверяет только цели, которых коснулся коммит. Тесты в pre-commit не запускаются.
