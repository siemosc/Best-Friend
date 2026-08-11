---
name: python-self-check
description: Самопроверка Python-кода после правок в Execute-режиме. Вызывай каждый раз после блока правок до того, как отдавать результат пользователю.
---

# Self-Check для BestFiend

## Где живёт инструментарий

Все QA-инструменты — в изолированном `uv`-проекте `tools/qa/` (отдельный venv, ноль prod-зависимостей). Точка входа: `tools/qa/dev_code_fix.py`. Это монорепный раннер, который сам перебирает 9 сервисов + 2 пакета и поднимает ruff/pyright/bandit/vulture/radon из своего venv. Сервисные `.venv` инструментов не содержат.

Полное описание архитектуры — в `.docs/conventions.md`, раздел «Тестирование и QA». Здесь — только регламент запуска.

## Когда запускать

Сразу после любой правки `.py` в `services/`, `packages/` или `tools/qa/`. Не в конце сессии, а каждый раз после блока правок, до того как отдавать результат пользователю.

Если QA-venv ещё не поднят — сначала:

```bash
uv sync --project tools/qa
```

Это идемпотентно, повторный запуск дёшев.

## Базовый цикл

1. **Default-прогон по затронутому**: imports + ruff + pyright **только** на сервисах, в которые попали правки.

   ```bash
   uv run --project tools/qa python tools/qa/dev_code_fix.py --service <name>
   ```

   Если правки в нескольких сервисах — перечисли их через несколько `--service` или передай список файлов:

   ```bash
   uv run --project tools/qa python tools/qa/dev_code_fix.py --from-files <file1> <file2> ...
   ```

   Если правка одна и не уверен, к какому сервису она относится — оставь раннеру самому через `--changed`:

   ```bash
   uv run --project tools/qa python tools/qa/dev_code_fix.py --changed
   ```

   `--changed` берёт изменения через `git status --porcelain` и сам маппит файлы в сервисы.

2. **Если ruff ругается** — гонишь lint с автофиксом, потом возвращаешься к шагу 1:

   ```bash
   uv run --project tools/qa python tools/qa/dev_code_fix.py --service <name> lint --fix
   ```

3. **Повторяй шаг 1 до `errors=0`.** Pyright-ошибки фикси руками — авто-фиксов для них нет.

4. **Полный аудит** запускай только по явному запросу пользователя или перед концом крупной задачи. Не каждый раз:

   ```bash
   uv run --project tools/qa python tools/qa/dev_code_fix.py --all all
   ```

   `all` добавляет к default-набору `bandit + vulture + radon`. `--all` означает «все цели монорепо», а не «все проверки» — это два разных флага.

## Какие наборы проверок есть

- **`default`** (блокирующий) — `imports + ruff + pyright`. Запускается по умолчанию, если режим не указан.
- **`lint`** — только ruff.
- **`types`** — только pyright.
- **`sec`** — только bandit.
- **`all`** — `default + bandit + vulture + radon`.
- **`test`** — тонкая обёртка над per-service pytest. **Не запускается в self-check автоматически.** Гонять только по явному запросу пользователя или перед PR.

## Где смотреть результаты

- **Консоль** — summary + список проблем. Печатается по умолчанию.
- **`tools/qa/.cache/report.json`** — полный JSON-отчёт пишется автоматически после каждого прогона. Внутри: `summary`, `checks[]` с командами и returncode'ами, `diagnostics[]` с полным контекстом каждой находки, `top_files`, `tests`. Сюда же лезть, если консольного вывода не хватило для понимания, что именно сломалось.
- **`tools/qa/.cache/pyright/<target>.json`** — сгенерированный pyright-конфиг для конкретной цели. Полезен для дебага «почему pyright не видит зависимость».

## Полезные команды

```bash
# Только ruff на одном сервисе
uv run --project tools/qa python tools/qa/dev_code_fix.py --service core lint

# Только типы на одном сервисе
uv run --project tools/qa python tools/qa/dev_code_fix.py --service core types

# Только security
uv run --project tools/qa python tools/qa/dev_code_fix.py --service core sec

# Pre-commit маршрут эмулировать вручную (как делает локальный pre-commit хук)
uv run --project tools/qa python tools/qa/dev_code_fix.py --from-files services/X/src/foo.py services/Y/src/bar.py

# Полный audit по всему монорепо (медленно)
uv run --project tools/qa python tools/qa/dev_code_fix.py --all all

# Тесты per-service (отдельная команда, не часть self-check)
uv run --project tools/qa python tools/qa/dev_code_fix.py --service notifications test
```

## Флаги

- **Селекторы целей** (взаимоисключающие):
  - `--changed` — по `git status --porcelain` (дефолт без флага)
  - `--service NAME` — конкретная цель, можно несколько раз
  - `--all` — все цели монорепо (9 сервисов + 2 пакета)
- `--from-files FILE...` — список файлов, маппится в цели по префиксу пути. Используется pre-commit'ом и при ручной эмуляции.
- `--fix` — включает `ruff --fix` + `ruff format` для режимов `lint` и `all`.
- `--context` — добавляет в stdout компактный `<analysis_context>` блок (полезно, когда хочется отдать результат другому агенту через текстовый канал).
- `--report-json PATH` — переопределить путь к JSON-отчёту. По умолчанию `tools/qa/.cache/report.json`.

## Что НЕ делать

- **Не запускать `--all` в обычном self-check.** `--all` — это полный прогон по всему монорепо, медленно. В дефолте используй `--changed` или `--service`.
- **Не запускать `test` в self-check.** Pytest требует поднятого окружения (env vars, иногда docker), долгий, и не входит в pre-commit. Гонять только по явному запросу.
- **Не редактировать конфиги ruff/pyright в сервисных `pyproject.toml`.** Единственный источник правды для ruff — корневой `pyproject.toml`. Pyright раннер генерит конфиг сам в `tools/qa/.cache/pyright/`. Если хочется поменять правила — корневой `[tool.ruff.*]`, а не сервисный.
- **Не запускать `dev_code_fix.py` напрямую** через `python tools/qa/dev_code_fix.py` без `uv run --project tools/qa`. Скрипт нуждается в QA-venv для подзапусков ruff/pyright/etc.
- **Не плодить кеши вне `tools/qa/.cache/`.** Всё временное QA пишет туда, эта папка в `.gitignore`.

## Pre-commit

Локальный pre-commit хук `dev-code-fix` запускает раннер с `--from-files` и `pass_filenames: true`. То есть при коммите автоматически прогоняется default-набор только по затронутым сервисам. Если ты только что внёс правки и собираешься коммитить — можно не делать ручной self-check, pre-commit сам прогонит то же самое. Но если правки большие и хочется отловить ошибки до коммита — гони руками через `--changed` или `--from-files`.

## Exit codes

- `0` — нет ошибок уровня `error`.
- `1` — есть хотя бы одна ошибка уровня `error` (либо упавший pytest при режиме `test`).
- `2` — ошибка CLI / неизвестная цель / git недоступен.

Self-check считается успешным при `0`. При `1` — фикси и перепрогоняй. При `2` — это не «код плохой», это «сам раннер не смог запуститься», читай stderr и чини окружение.
