-- Миграция 005: снос PG-registry артефактов (переезд на SeaweedFS-only).
--
-- Метаданные артефактов теперь живут в S3-объекте meta.json рядом с байтами,
-- а не в Postgres. Таблица, её индексы и constraint падают вместе (CASCADE).
-- Инкрементально (001-004 уже в _migrations).

DROP TABLE IF EXISTS core.artifacts_registry CASCADE;
