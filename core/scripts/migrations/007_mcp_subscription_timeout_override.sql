-- Миграция 007: per-user timeout override на подписке MCP.
--
-- timeout_s на mcp_subscriptions — nullable: NULL = нет персонального оверрайда
-- (резолв list_for_user берёт connection.timeout_s через COALESCE). Даёт юзеру
-- крутить таймаут исполнения тулзов «для себя», не трогая дефолт сервера.
-- Forward-only. Инкрементально (001-006 уже в _migrations).

ALTER TABLE core.mcp_subscriptions ADD COLUMN timeout_s real;
ALTER TABLE core.mcp_subscriptions ADD CONSTRAINT chk_mcp_subscriptions_timeout
    CHECK (timeout_s IS NULL OR (timeout_s >= 1.0 AND timeout_s <= 300.0));
