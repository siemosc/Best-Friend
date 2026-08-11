-- Миграция 015: measurements — числовые ряды жизни (пилот measurements-тракта).
--
-- Дизайн: .claude/docs/lifelong-memory-architecture.md. Одна таблица закрывает
-- и измерения (metric + value: вес, часы сна), и события-счётчики (value NULL:
-- зал, приём пищи — сама строка и есть факт события). Агрегаты — чистый SQL,
-- LLM не на пути числового потока.
--
-- Bitemporal как в notes: event_time — когда произошло в мире,
-- observed_at — когда записала система.
--
-- Строго аддитивная: существующие таблицы не затрагивает.

CREATE TABLE core.measurements (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id      uuid NOT NULL REFERENCES core.users(user_id) ON DELETE CASCADE,
    metric       text NOT NULL,
    value        double precision,
    unit         text,
    tags         jsonb NOT NULL DEFAULT '{}'::jsonb,
    -- Провенанс писателя: 'tool' — агентная тулза; задел под 'connector:*'.
    source       text NOT NULL DEFAULT 'tool',
    event_time   timestamp with time zone NOT NULL,
    observed_at  timestamp with time zone DEFAULT now() NOT NULL
);

-- Основной путь чтения: агрегаты одной метрики за период.
CREATE INDEX idx_measurements_user_metric_time ON core.measurements (user_id, metric, event_time DESC);
-- Сводка всех метрик за период (memory_stats без metric, недельный дайджест).
CREATE INDEX idx_measurements_user_time ON core.measurements (user_id, event_time DESC);
