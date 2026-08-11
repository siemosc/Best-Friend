# Backlog

Приоритизированный список задач. Верх — приоритетнее. (Бывший `.claude/docs/backlog.md`; папка docs снесена 2026-07-03 — документация живёт в Serena memories.)

## Память / lifelong (рамка: `mem:lifelong_memory_architecture`)

1. **Live-смок measurements-тракта** — в Telegram: «запиши вес 70.2» → memory_track; «сколько раз я был в зале?» → memory_stats; дождаться sleep-цикла, проверить дайджест в сводке недели.
2. **Ingestion-политика источников + staging** — декларативный конфиг источника (судьба: live/телеметрия/память; хранить ли raw), append-only staging с watermark для не-диалоговых коннекторов (тираж паттерна turns+watermarks), обобщение провенанса notes (`source_turn_*` → turn | source). Пререквизит внешних коннекторов (календарь, git, health).
3. **Ambient-блок live-сигналов** — push время+локация в init рядом с environment; 3–5 строк, расширять по доказанной частоте.
4. **Семантические дубли метрик** — alias-механизм или sleep-дедуп имён, по реальной картине пилота.
5. **Typed edges entity↔entity** — только при доказанной боли multi-hop recall.

## Долг рефакторинга 2026-07 (серии v29.9–v29.10 завершены, хвосты)

- **Живой Telegram-смок** — текст+стрим, фото, media group, `/web`, доставка артефакта; после всей серии не проводился (заодно закроет давний live-смок Observer/sleep).
- **Недобранные lifecycle-тесты**: rollback-параметризация по каждой стадии старта (есть только artifacts), повторный `stop()` без второго cleanup, извлечение исключения supervisor'ом, `test_lifecycle.py`.
- **Media-group flush** (`telegram/attachment_ingest.py`, call_later → create_task) — detached-задача вне BackgroundTaskSupervisor.
- **«позавчера» матчится как «вчера»** (`memory/recall/time_markers.py`) — подстрочный `in`-матч + порядок словаря; pre-existing.
- **`memory/entities/` без юнит-тестов** — есть только фейк в `tests/memory/fakes/entities.py`; резолв алиасов и `match_in_text` не покрыты.
- **`graph/prompts/` — пакет из одного модуля** (`environment.py`), при том что промпты react лежат в `graph/nodes/react/prompts.py`. Единственный потребитель `render_environment` — `graph/nodes/init/node.py`. Два дома промптов, решение отложено.

## Прочее

- **Дроп схемы contextforge в живой БД** — отдельная инфра-операция (init-скрипт уже чист).
- Деплой-контейнеризация core (Dockerfile.service снесён, замены нет).
