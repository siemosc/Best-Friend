-- Миграция 006: пересборка MCP-storage (модель A: сервер + подписка) + снос CF-археологии.
--
-- Сносим три поколения MCP-storage (все осиротевшие после сноса ContextForge,
-- коммит 0911338) и cf-колонки users. Создаём mcp_connections (определение
-- сервера, один на всех) + mcp_subscriptions (user<->connection: персональный
-- токен, enabled, denylist тулзов). Forward-only, одна транзакция.
-- Инкрементально (001-005 уже в _migrations).

-- ── DROP: три поколения MCP-storage + cf-колонки users ──
DROP TABLE IF EXISTS core.user_mcp_configs CASCADE;
DROP TABLE IF EXISTS core.mcp_servers CASCADE;
DROP TABLE IF EXISTS core.user_gateways CASCADE;
DROP TABLE IF EXISTS core.built_in_gateways CASCADE;

ALTER TABLE core.users
    DROP COLUMN IF EXISTS cf_email CASCADE,    -- снимет users_cf_email_key + idx_users_cf_email
    DROP COLUMN IF EXISTS cf_team_id;

-- ── CREATE: mcp_connections (определение сервера) ──
CREATE TABLE core.mcp_connections (
    connection_id uuid DEFAULT gen_random_uuid() NOT NULL,
    name          character varying(64)  NOT NULL,
    url           character varying(500) NOT NULL,
    transport     character varying(16)  DEFAULT 'http_stream' NOT NULL,
    auth_type     character varying(16)  DEFAULT 'none'        NOT NULL,
    is_public     boolean DEFAULT false NOT NULL,
    is_system     boolean DEFAULT false NOT NULL,
    timeout_s     real    DEFAULT 30.0  NOT NULL,
    created_at    timestamp with time zone DEFAULT now() NOT NULL,
    updated_at    timestamp with time zone,
    CONSTRAINT mcp_connections_pkey PRIMARY KEY (connection_id),
    CONSTRAINT mcp_connections_name_key UNIQUE (name),
    CONSTRAINT chk_mcp_connections_transport CHECK (transport IN ('http_stream')),
    CONSTRAINT chk_mcp_connections_auth_type CHECK (auth_type IN ('none', 'bearer'))
);

-- ── CREATE: mcp_subscriptions (user <-> connection) ──
CREATE TABLE core.mcp_subscriptions (
    user_id        uuid NOT NULL,
    connection_id  uuid NOT NULL,
    auth_token     text,
    enabled        boolean DEFAULT true NOT NULL,
    disabled_tools jsonb   DEFAULT '[]'::jsonb NOT NULL,
    created_at     timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT mcp_subscriptions_pkey PRIMARY KEY (user_id, connection_id),
    CONSTRAINT mcp_subscriptions_user_id_fkey
        FOREIGN KEY (user_id) REFERENCES core.users(user_id) ON DELETE CASCADE,
    CONSTRAINT mcp_subscriptions_connection_id_fkey
        FOREIGN KEY (connection_id) REFERENCES core.mcp_connections(connection_id) ON DELETE CASCADE
);

-- ── Индексы ──
-- partial по is_public: горячий путь резолва (public-ветка union) + список public-каталога.
CREATE INDEX idx_mcp_connections_public ON core.mcp_connections USING btree (is_public) WHERE (is_public = true);
-- по user_id: list_by_user + private-ветка резолва (LEFT JOIN по user_id).
CREATE INDEX idx_mcp_subscriptions_user_id ON core.mcp_subscriptions USING btree (user_id);
-- по connection_id: обслуживает ON DELETE CASCADE с mcp_connections (PK не покрывает поиск по одному connection_id).
CREATE INDEX idx_mcp_subscriptions_connection_id ON core.mcp_subscriptions USING btree (connection_id);
