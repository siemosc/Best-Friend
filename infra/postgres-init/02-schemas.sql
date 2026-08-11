-- Схема core внутри базы bestfiend:
--   core   — таблицы монолита (control_plane, memory, artifacts) + реестр _migrations
--   public — расширения (vector, pg_trgm), таблиц нет
--
-- Выполняется один раз при первой инициализации volume контейнера postgres
-- (до открытия TCP-листенера — схема готова к старту core).
--
-- ALTER ROLE задаёт search_path по умолчанию для роли bestfiend; core дополнительно
-- переопределяет его явно через asyncpg server_settings (search_path=core,public).
--
-- Если volume уже существует (init-скрипты не переигрываются) — применить вручную:
--   docker exec -it bestfiend-postgres psql -U bestfiend -d bestfiend \
--     -c "CREATE SCHEMA IF NOT EXISTS core;" \
--     -c "ALTER ROLE bestfiend SET search_path = core, public;"

CREATE SCHEMA IF NOT EXISTS core;

ALTER ROLE bestfiend SET search_path = core, public;
