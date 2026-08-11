-- Миграция 008: per-server флаг concurrency MCP-вызовов.
--
-- supports_parallel_tool_calls=false → граф сериализует вызовы к этому серверу
-- (семафор=1 в tools-ноде); вызовы к остальным серверам остаются параллельными.
-- Дефолт true сохраняет текущее параллельное поведение. Forward-only.
-- Инкрементально (001-007 уже в _migrations).

ALTER TABLE core.mcp_connections
    ADD COLUMN supports_parallel_tool_calls boolean DEFAULT true NOT NULL;
