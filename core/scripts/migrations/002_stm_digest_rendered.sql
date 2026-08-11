-- Миграция 002: вынос дайджеста в отдельную rendered-колонку (two-phase запись).
--
-- digest_rendered хранит готовые куски дайджеста как jsonb:
--   assistant(tool_calls=[work_summary]) + tool(content=digest).
-- Пишется второй фазой (фоном) после вставки пары; на чтении вклеивается между
-- bookends [user, assistant]. Колонка work_digest (text) остаётся под сырой текст
-- (трейс + оценка бюджета).
--
-- TRUNCATE: строки старого формата держат дайджест впечённым в монолитный rendered
-- (4 записи), а новый read-путь ждёт bookends (2 записи) + отдельный digest_rendered.
-- Реальных пользователей пока нет — чистим, чтобы форматы не смешивались.

ALTER TABLE core.stm ADD COLUMN digest_rendered jsonb;
TRUNCATE core.stm;
