# Конвенции кода

## Язык

- Код (имена, идентификаторы) — английский.
- **Комментарии, докстринги, логи, документация, сообщения коммитов — русский.**
- Коммиты: `vXX.Y.Z - тип: описание по-русски` (пример: `v29.6.0 - feat: динамический read-бюджет...`). Версия проекта живёт в заголовке коммита, не в pyproject.

## Ruff (единый конфиг в корневом pyproject.toml)

- line-length 88, target py311, double quotes, space indent.
- Правила: E, F, I, UP, B, SIM, C4, TID, ICN, PIE; E501 отключён (длину держит форматтер).
- **Относительные импорты уровня родителя запрещены** (`ban-relative-imports = parents`) — межмодульные импорты только абсолютные `bestfiend.*`; в тестах — абсолютные `tests.*`.
- isort: force-sort-within-sections, 2 пустые строки после импортов, first-party = `bestfiend`, `tests`.
- B008 ослаблен для FastAPI: `Depends()/Query()/Body()/...` в default args — норма.

## Структура модуля (core/src/bestfiend/<module>/)

Повторяющийся паттерн — у модуля свои:
- `contracts.py` — pydantic-модели границы модуля;
- `errors.py` — доменная иерархия ошибок (не сырые ValueError);
- `settings.py` — pydantic-settings конфиг модуля;
- `runtime.py` — сборка/владение ресурсами модуля (DI-точка).

Кросс-модульные контракты — в `bestfiend/contracts/` (user_environment, artifacts, request_correlation, events, mcp).

## Принципы

- DI: зависимости передаются явно через runtime/конструкторы, без глобального состояния.
- Fail fast: guard clauses, ранние возвраты.
- Валидация на границах (HTTP, Telegram, MCP, БД), внутри домена — доверие типам.
- Внешний I/O (HTTP, DB, LLM, S3) — за клиентом/адаптером.

## Локальные паттерны

- **Wire-уровень при рефакторингах не трогать**: поля сериализуемых моделей (ArtifactRef, notes, meta.json — включая значения вроде art_source="gateway_telegram"), JSON-ключи хранилищ, HTTP-пути, header-строки, схема БД. Переименования кода wire-имён не касаются.
- Production-assert запрещён — вместо него доменные ошибки; assert только в тестах.
- Подавление bandit B608 — точечное: `# nosec B608 — SQL из внутренних констант, значения через $N-параметры`. Для тройных f-строк nosec ставится на ЗАКРЫВАЮЩЕЙ `"""`-строке (bandit засчитывает по linerange; в конец открывающей нельзя — попадёт в SQL-литерал). Baseline-файла нет.
- pydantic-settings класс с validation_alias, создаваемый без аргументов (`GraphSettings()`), триггерит pyright reportCallIssue → гасить `# pyright: ignore[reportCallIssue]` (прецеденты: graph/runtime.py, tests/graph/test_runtime.py).
- Дефолты, нужные и settings-классу, и другим потребителям (поля GraphContext), — общие константы `*_DEFAULT` рядом с settings (один источник, graph/config.py).
- **`from __future__ import annotations` не используем** (снесён из всех модулей core 2026-07-10; правило-предшественник требовало обратного и держалось только на самом себе). Отложенных импортов тоже нет: `TYPE_CHECKING` в core пуст, типы импортируются обычным образом. Самоссылка в аннотации — `typing.Self`; forward-ref на класс ниже по файлу — строковая аннотация (единственный прецедент: `StreamPublisher.open`).
- vulture запускается с `--ignore-names cls` (tools/qa/dev_code_fix.py): из имён параметров он игнорирует только `self`, и `cls` в classmethod-валидаторах pydantic считает мёртвой переменной.

## Тесты

- pytest, `asyncio_mode = "strict"` — async-тесты помечать `@pytest.mark.asyncio` явно.
- Раскладка `core/tests/` зеркалит пакеты `src/bestfiend/` по именам: `memory/turns/`, `memory/web_facade/`, `app/routes/mcp/`. Подпакет без своего каталога тестов — норма, если тесты лежат в родителе.
- Тестовые двойники — в `tests/<module>/fakes/`, суффикс `Fake`, имя зеркалит production-класс (`NoteRepository` → `NoteRepositoryFake`). Импорт всегда абсолютный `tests.*`: относительный `.fakes` тихо меняет смысл при переезде файла.
- В `conftest.py` — только фикстуры. Фабрика, нужная нескольким тест-пакетам (`build_observer_service`, `stub_observer_llm`), живёт в `fakes/`, а не импортируется из чужого conftest.