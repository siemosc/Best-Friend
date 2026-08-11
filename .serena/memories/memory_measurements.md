# Measurements-тракт памяти (пилот lifelong-memory, 2026-07-03, Codex APPROVED)

Числовые ряды жизни в памяти: `core.measurements` (миграция `015_measurements.sql`) — измерения (metric+value: вес, сон) и события-счётчики (value NULL: зал, еда) одной таблицей. Bitemporal (event_time/observed_at), `tags jsonb`, `source` ('tool', задел под 'connector:*').

## Компоненты
- `core/src/bestfiend/memory/measurements/` — contracts (`MeasurementDraft`, `MetricAggregate`, `normalize_metric_name`), repository (`insert` → `(id, is_new)`, `aggregate` — count/avg/min/max/sum/last_value, бакеты day/week/month через date_trunc с whitelist), render (текстовые строки агрегатов, обрезка хвостом 40 строк).
- Тулзы в `memory/agent_tools/`: `memory_track` (записать точку; канонизация имени форматом lower/underscore; naive datetime → UTC), `memory_stats` (агрегаты; без metric — обзор всех метрик; to_date включительно: полуночная граница +1 день). Обе в `MEMORY_TOOL_NAMES` → биндятся top-level автоматически.
- Sleep: `SleepContext.measurements`; `period_summaries` получает дайджест агрегатов недели в промпт; гейт расширен — неделя без наблюдений, но с измерениями, получает сводку.
- Ops-лог на измерения НЕ пишется (`MemoryOperation` привязан к note_id).

## Контекст
Концепт-рамка (знать ≠ помнить, судьбу определяет источник, typed edges отложены): `mem:lifelong_memory_architecture`. Очередь работ и принятые риски: `mem:backlog` (live-смок не проводился; семантические дубли метрик — смотреть по реальной картине).
