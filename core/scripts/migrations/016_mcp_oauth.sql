-- Миграция 016: OAuth2-авторизация пользователей в MCP-серверах.
--
-- Расширяем CHECK auth_type третьим значением 'oauth' и добавляем три таблицы
-- OAuth-тракта:
--   mcp_oauth_clients — OAuth-клиент per connection (предрегистрированный или DCR);
--   mcp_oauth_flows   — незавершённые авторизации, одноразовые (state + PKCE в БД,
--                       flow разорван на два HTTP-запроса start/callback);
--   mcp_oauth_tokens  — выданные токены per (user, connection), обновляются по refresh.
--
-- core.models не затрагивается: тракт read/write идёт через свои репозитории,
-- доменные модели памяти остаются как есть.
-- Forward-only, строго аддитивная (кроме пересборки одного CHECK).
-- Таблицы и _migrations живут в схеме core (search_path=core,public).

ALTER TABLE core.mcp_connections DROP CONSTRAINT chk_mcp_connections_auth_type;
ALTER TABLE core.mcp_connections ADD CONSTRAINT chk_mcp_connections_auth_type
    CHECK (auth_type IN ('none', 'bearer', 'oauth'));

CREATE TABLE core.mcp_oauth_clients (               -- OAuth-клиент per connection
    connection_id uuid PRIMARY KEY REFERENCES core.mcp_connections(connection_id) ON DELETE CASCADE,
    client_id text NOT NULL,
    client_secret text,                        -- NULL для public-клиентов (DCR без секрета)
    token_endpoint_auth_method text NOT NULL   -- без DEFAULT: значение всегда пишет сервис
        CHECK (token_endpoint_auth_method IN ('client_secret_basic', 'client_secret_post', 'none')),
    source text NOT NULL CHECK (source IN ('preregistered', 'dcr')),
    client_secret_expires_at timestamptz,      -- DCR может выдать протухающий секрет
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz,
    CONSTRAINT chk_mcp_oauth_clients_secret_for_method
        CHECK (token_endpoint_auth_method = 'none' OR client_secret IS NOT NULL)
);
CREATE TABLE core.mcp_oauth_flows (                 -- незавершённые авторизации, одноразовые
    state text PRIMARY KEY,
    user_id uuid NOT NULL REFERENCES core.users(user_id) ON DELETE CASCADE,
    connection_id uuid NOT NULL REFERENCES core.mcp_connections(connection_id) ON DELETE CASCADE,
    code_verifier text NOT NULL,
    redirect_uri text NOT NULL,
    token_endpoint text NOT NULL,              -- из discovery на start, чтобы callback не переоткрывал
    issuer text NOT NULL,                      -- ожидаемый AS issuer: сверка `iss` из callback (RFC 9207)
    resource text NOT NULL,                    -- RFC 8707, одинаковый в обоих запросах
    scope text,
    expires_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE core.mcp_oauth_tokens (                -- токены per (user, connection)
    user_id uuid NOT NULL REFERENCES core.users(user_id) ON DELETE CASCADE,
    connection_id uuid NOT NULL REFERENCES core.mcp_connections(connection_id) ON DELETE CASCADE,
    access_token text NOT NULL,
    refresh_token text,
    expires_at timestamptz,                    -- NULL = AS не сообщил срок
    scope text,
    token_endpoint text NOT NULL,              -- для refresh без re-discovery
    refresh_failed_at timestamptz,             -- момент отказа refresh (invalid_grant) → статус expired
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz,
    PRIMARY KEY (user_id, connection_id)
);
