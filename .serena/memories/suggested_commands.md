# Команды

Все команды — из корня репо, если не сказано иное. ОС — Windows, но dev-скрипты — bash (Git Bash); PowerShell-обёртки не заводить.

## Dev-стек

```bash
bash scripts/dev.sh                 # полный стек: Docker + core + web
bash scripts/dev.sh --no-ui         # без web
bash scripts/dev.sh --services-only # без Docker
bash scripts/dev.sh --infra-only    # только Docker-инфра
```

Только core: `uv run --directory core python -m bestfiend` (порт 8010, env `CORE_PORT`).
Только web: `pnpm dev` из `web/` (порт 5173).

## Окружения

```bash
cd core && uv sync            # venv core
uv sync --project tools/qa    # venv QA (один раз)
cd web && pnpm install        # web
```

## Docker

```bash
docker compose up -d                          # инфра: postgres + seaweedfs
docker compose up -d postgres                 # точечно один сервис
docker compose --profile app up -d --build    # + контейнерные core и web (деплой)
docker compose --profile "*" down             # остановить всё
```

## QA-раннер

```bash
uv run --project tools/qa python tools/qa/dev_code_fix.py                      # по git diff
uv run --project tools/qa python tools/qa/dev_code_fix.py --all                # полный прогон
uv run --project tools/qa python tools/qa/dev_code_fix.py --service core lint  # точечно: lint|types|...
```

⚠️ Гочи раннера:
- `--changed` / `--from-files` не мапят пути `core/` на цель — для core всегда явно `--service core`.
- На Windows перед запуском ставить `PYTHONIOENCODING=utf-8`, иначе падает на выводе кириллицы. Гоча общая для Python-CLI с юникод-выводом (cp1251-консоль): `serena memories check` требует того же.
- Раннер не автофиксит I001 (isort) — после массовых правок импортов сперва `uv run --directory core ruff check --fix`, потом QA.

## Гочи правок кода (тулинг)

- **Serena rename_symbol на этом репо не использовать.** LSP-офсеты разъезжаются на кириллице в докстрингах/комментариях и корёжат файлы даже при одиночном rename; плюс тул не видит core/tests (вне LSP-workspace). Переименования — sed по word-boundary + контрольный grep.
- sed `\b` не срабатывает внутри snake_case-имён (производное test_read_x_... не матчится по `\bread_x\b`) — производные имена добивать отдельным sed по полному имени.
- Grep-тул: lookbehind `(?<!...)` молча не матчит (ripgrep без PCRE2) — простые паттерны + ручная фильтрация.

## Тесты

```bash
uv run --directory core pytest              # все тесты core
uv run --directory core pytest tests/memory # точечно
```

## Windows-специфика

- Git Bash доступен: `bash scripts/dev.sh` работает как есть.
- Пути с прямыми слэшами понимает и PowerShell, и bash.
- `uv run --directory <svc>` / `--project <svc>` избавляет от ручной активации venv.
- «Failed to canonicalize script path» — это uv-trampoline на venv-шимах (.exe: pytest, pre-commit), не сам инструмент; ловится при exec шима из git-хука или `uv run <шим>`. Лечение: `python -m <tool>` вместо шима.
- Pre-commit хук в `.git/hooks` хардкодит путь питона того клона, из которого ставился, — при переносе/копии репо переустанавливать: `python -m pre_commit install -f`.