--
-- PostgreSQL database dump
--

\restrict jTFDbudA7ef2gkmdwecgwFD5pv5BHl6qXuHcbMCt5hsWotpqbQQP5F0GzYHjjvS

-- Dumped from database version 16.13 (Debian 16.13-1.pgdg12+1)
-- Dumped by pg_dump version 16.13 (Debian 16.13-1.pgdg12+1)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: core; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA core;


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: artifacts_registry; Type: TABLE; Schema: core; Owner: -
--

CREATE TABLE core.artifacts_registry (
    artifact_id character varying(120) NOT NULL,
    user_id uuid NOT NULL,
    art_source character varying(80) NOT NULL,
    request_id character varying(255) NOT NULL,
    type character varying(32) NOT NULL,
    description character varying(500) NOT NULL,
    path text NOT NULL,
    status character varying(20) DEFAULT 'creating'::character varying NOT NULL,
    error_code character varying(64),
    needs_cleanup boolean DEFAULT false NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone,
    semantic_name character varying(120) DEFAULT ''::character varying NOT NULL,
    CONSTRAINT chk_artifacts_registry_status CHECK (((status)::text = ANY ((ARRAY['creating'::character varying, 'active'::character varying, 'failed'::character varying, 'expired'::character varying])::text[])))
);


--
-- Name: auth_binding_codes; Type: TABLE; Schema: core; Owner: -
--

CREATE TABLE core.auth_binding_codes (
    code character varying(6) NOT NULL,
    user_id uuid NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: built_in_gateways; Type: TABLE; Schema: core; Owner: -
--

CREATE TABLE core.built_in_gateways (
    name character varying(64) NOT NULL,
    cf_gateway_id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: mcp_servers; Type: TABLE; Schema: core; Owner: -
--

CREATE TABLE core.mcp_servers (
    server_id uuid DEFAULT gen_random_uuid() NOT NULL,
    name character varying(64) NOT NULL,
    url character varying(500) NOT NULL,
    enabled boolean DEFAULT true NOT NULL,
    execution_mode character varying(16) DEFAULT 'direct'::character varying NOT NULL,
    timeout_s real DEFAULT 30.0 NOT NULL,
    bestfiend_mcp boolean DEFAULT true NOT NULL,
    adapter_config jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone,
    CONSTRAINT chk_mcp_execution_mode CHECK (((execution_mode)::text = ANY ((ARRAY['direct'::character varying, 'llm_single'::character varying, 'react'::character varying])::text[])))
);


--
-- Name: models; Type: TABLE; Schema: core; Owner: -
--

CREATE TABLE core.models (
    id text NOT NULL,
    name text NOT NULL,
    config jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: sessions; Type: TABLE; Schema: core; Owner: -
--

CREATE TABLE core.sessions (
    session_id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    expires_at timestamp with time zone NOT NULL
);


--
-- Name: stm; Type: TABLE; Schema: core; Owner: -
--

CREATE TABLE core.stm (
    id bigint NOT NULL,
    user_id uuid NOT NULL,
    user_message text NOT NULL,
    assistant_message text NOT NULL,
    origin character varying(16) NOT NULL,
    origin_source character varying(64) NOT NULL,
    request_id text NOT NULL,
    gap_seconds integer,
    created_at timestamp with time zone NOT NULL,
    work_digest text,
    rendered jsonb,
    pair_token_count integer,
    attached_artifacts jsonb DEFAULT '[]'::jsonb NOT NULL,
    created_artifacts jsonb DEFAULT '[]'::jsonb NOT NULL
);


--
-- Name: stm_id_seq; Type: SEQUENCE; Schema: core; Owner: -
--

CREATE SEQUENCE core.stm_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: stm_id_seq; Type: SEQUENCE OWNED BY; Schema: core; Owner: -
--

ALTER SEQUENCE core.stm_id_seq OWNED BY core.stm.id;


--
-- Name: user_assistant_configs; Type: TABLE; Schema: core; Owner: -
--

CREATE TABLE core.user_assistant_configs (
    user_id uuid NOT NULL,
    work_instruction text DEFAULT ''::text NOT NULL,
    answer_instruction text DEFAULT ''::text NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    intent_instruction text DEFAULT ''::text NOT NULL,
    action_instruction text DEFAULT ''::text NOT NULL,
    overrides jsonb DEFAULT '{}'::jsonb NOT NULL
);


--
-- Name: user_gateways; Type: TABLE; Schema: core; Owner: -
--

CREATE TABLE core.user_gateways (
    user_id uuid NOT NULL,
    cf_gateway_id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: user_mcp_configs; Type: TABLE; Schema: core; Owner: -
--

CREATE TABLE core.user_mcp_configs (
    user_id uuid NOT NULL,
    server_id uuid NOT NULL,
    enabled boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: users; Type: TABLE; Schema: core; Owner: -
--

CREATE TABLE core.users (
    user_id uuid DEFAULT gen_random_uuid() NOT NULL,
    telegram_chat_id bigint,
    locale character varying(10) DEFAULT 'ru'::character varying NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone,
    role character varying(16) DEFAULT 'user'::character varying NOT NULL,
    status character varying(16) DEFAULT 'pending'::character varying NOT NULL,
    discord_user_id character varying(64),
    login character varying(64),
    password_hash text,
    timezone character varying(64) DEFAULT 'Europe/Belgrade'::character varying NOT NULL,
    city character varying(128),
    country character varying(128),
    cf_email character varying(255),
    cf_team_id uuid,
    CONSTRAINT chk_users_role CHECK (((role)::text = ANY ((ARRAY['user'::character varying, 'admin'::character varying])::text[]))),
    CONSTRAINT chk_users_status CHECK (((status)::text = ANY ((ARRAY['pending'::character varying, 'active'::character varying, 'banned'::character varying])::text[])))
);


--
-- Name: stm id; Type: DEFAULT; Schema: core; Owner: -
--

ALTER TABLE ONLY core.stm ALTER COLUMN id SET DEFAULT nextval('core.stm_id_seq'::regclass);


--
-- Name: artifacts_registry artifacts_registry_pkey; Type: CONSTRAINT; Schema: core; Owner: -
--

ALTER TABLE ONLY core.artifacts_registry
    ADD CONSTRAINT artifacts_registry_pkey PRIMARY KEY (artifact_id);


--
-- Name: auth_binding_codes auth_binding_codes_pkey; Type: CONSTRAINT; Schema: core; Owner: -
--

ALTER TABLE ONLY core.auth_binding_codes
    ADD CONSTRAINT auth_binding_codes_pkey PRIMARY KEY (code);


--
-- Name: built_in_gateways built_in_gateways_cf_gateway_id_key; Type: CONSTRAINT; Schema: core; Owner: -
--

ALTER TABLE ONLY core.built_in_gateways
    ADD CONSTRAINT built_in_gateways_cf_gateway_id_key UNIQUE (cf_gateway_id);


--
-- Name: built_in_gateways built_in_gateways_pkey; Type: CONSTRAINT; Schema: core; Owner: -
--

ALTER TABLE ONLY core.built_in_gateways
    ADD CONSTRAINT built_in_gateways_pkey PRIMARY KEY (name);


--
-- Name: mcp_servers mcp_servers_name_key; Type: CONSTRAINT; Schema: core; Owner: -
--

ALTER TABLE ONLY core.mcp_servers
    ADD CONSTRAINT mcp_servers_name_key UNIQUE (name);


--
-- Name: mcp_servers mcp_servers_pkey; Type: CONSTRAINT; Schema: core; Owner: -
--

ALTER TABLE ONLY core.mcp_servers
    ADD CONSTRAINT mcp_servers_pkey PRIMARY KEY (server_id);


--
-- Name: models models_pkey; Type: CONSTRAINT; Schema: core; Owner: -
--

ALTER TABLE ONLY core.models
    ADD CONSTRAINT models_pkey PRIMARY KEY (id);


--
-- Name: sessions sessions_pkey; Type: CONSTRAINT; Schema: core; Owner: -
--

ALTER TABLE ONLY core.sessions
    ADD CONSTRAINT sessions_pkey PRIMARY KEY (session_id);


--
-- Name: stm stm_pkey; Type: CONSTRAINT; Schema: core; Owner: -
--

ALTER TABLE ONLY core.stm
    ADD CONSTRAINT stm_pkey PRIMARY KEY (id);


--
-- Name: user_assistant_configs user_assistant_configs_pkey; Type: CONSTRAINT; Schema: core; Owner: -
--

ALTER TABLE ONLY core.user_assistant_configs
    ADD CONSTRAINT user_assistant_configs_pkey PRIMARY KEY (user_id);


--
-- Name: user_gateways user_gateways_pkey; Type: CONSTRAINT; Schema: core; Owner: -
--

ALTER TABLE ONLY core.user_gateways
    ADD CONSTRAINT user_gateways_pkey PRIMARY KEY (cf_gateway_id);


--
-- Name: user_mcp_configs user_mcp_configs_pkey; Type: CONSTRAINT; Schema: core; Owner: -
--

ALTER TABLE ONLY core.user_mcp_configs
    ADD CONSTRAINT user_mcp_configs_pkey PRIMARY KEY (user_id, server_id);


--
-- Name: users users_cf_email_key; Type: CONSTRAINT; Schema: core; Owner: -
--

ALTER TABLE ONLY core.users
    ADD CONSTRAINT users_cf_email_key UNIQUE (cf_email);


--
-- Name: users users_pkey; Type: CONSTRAINT; Schema: core; Owner: -
--

ALTER TABLE ONLY core.users
    ADD CONSTRAINT users_pkey PRIMARY KEY (user_id);


--
-- Name: users users_telegram_chat_id_key; Type: CONSTRAINT; Schema: core; Owner: -
--

ALTER TABLE ONLY core.users
    ADD CONSTRAINT users_telegram_chat_id_key UNIQUE (telegram_chat_id);


--
-- Name: idx_artifacts_registry_request_id; Type: INDEX; Schema: core; Owner: -
--

CREATE INDEX idx_artifacts_registry_request_id ON core.artifacts_registry USING btree (request_id);


--
-- Name: idx_artifacts_registry_semantic_name; Type: INDEX; Schema: core; Owner: -
--

CREATE INDEX idx_artifacts_registry_semantic_name ON core.artifacts_registry USING btree (semantic_name) WHERE ((semantic_name)::text <> ''::text);


--
-- Name: idx_artifacts_registry_status; Type: INDEX; Schema: core; Owner: -
--

CREATE INDEX idx_artifacts_registry_status ON core.artifacts_registry USING btree (status);


--
-- Name: idx_artifacts_registry_user_source; Type: INDEX; Schema: core; Owner: -
--

CREATE INDEX idx_artifacts_registry_user_source ON core.artifacts_registry USING btree (user_id, art_source, created_at);


--
-- Name: idx_auth_binding_codes_expires_at; Type: INDEX; Schema: core; Owner: -
--

CREATE INDEX idx_auth_binding_codes_expires_at ON core.auth_binding_codes USING btree (expires_at);


--
-- Name: idx_auth_binding_codes_user_id; Type: INDEX; Schema: core; Owner: -
--

CREATE UNIQUE INDEX idx_auth_binding_codes_user_id ON core.auth_binding_codes USING btree (user_id);


--
-- Name: idx_mcp_servers_enabled; Type: INDEX; Schema: core; Owner: -
--

CREATE INDEX idx_mcp_servers_enabled ON core.mcp_servers USING btree (enabled) WHERE (enabled = true);


--
-- Name: idx_sessions_expires_at; Type: INDEX; Schema: core; Owner: -
--

CREATE INDEX idx_sessions_expires_at ON core.sessions USING btree (expires_at);


--
-- Name: idx_sessions_user_id; Type: INDEX; Schema: core; Owner: -
--

CREATE INDEX idx_sessions_user_id ON core.sessions USING btree (user_id);


--
-- Name: idx_stm_user_created; Type: INDEX; Schema: core; Owner: -
--

CREATE INDEX idx_stm_user_created ON core.stm USING btree (user_id, created_at DESC);


--
-- Name: idx_user_gateways_user_id; Type: INDEX; Schema: core; Owner: -
--

CREATE INDEX idx_user_gateways_user_id ON core.user_gateways USING btree (user_id);


--
-- Name: idx_user_mcp_configs_user_id; Type: INDEX; Schema: core; Owner: -
--

CREATE INDEX idx_user_mcp_configs_user_id ON core.user_mcp_configs USING btree (user_id);


--
-- Name: idx_users_cf_email; Type: INDEX; Schema: core; Owner: -
--

CREATE INDEX idx_users_cf_email ON core.users USING btree (cf_email) WHERE (cf_email IS NOT NULL);


--
-- Name: idx_users_discord_user_id; Type: INDEX; Schema: core; Owner: -
--

CREATE UNIQUE INDEX idx_users_discord_user_id ON core.users USING btree (discord_user_id) WHERE (discord_user_id IS NOT NULL);


--
-- Name: idx_users_login; Type: INDEX; Schema: core; Owner: -
--

CREATE UNIQUE INDEX idx_users_login ON core.users USING btree (login) WHERE (login IS NOT NULL);


--
-- Name: idx_users_status_pending; Type: INDEX; Schema: core; Owner: -
--

CREATE INDEX idx_users_status_pending ON core.users USING btree (status) WHERE ((status)::text = 'pending'::text);


--
-- Name: idx_users_telegram_chat_id; Type: INDEX; Schema: core; Owner: -
--

CREATE INDEX idx_users_telegram_chat_id ON core.users USING btree (telegram_chat_id);


--
-- Name: auth_binding_codes auth_binding_codes_user_id_fkey; Type: FK CONSTRAINT; Schema: core; Owner: -
--

ALTER TABLE ONLY core.auth_binding_codes
    ADD CONSTRAINT auth_binding_codes_user_id_fkey FOREIGN KEY (user_id) REFERENCES core.users(user_id) ON DELETE CASCADE;


--
-- Name: sessions sessions_user_id_fkey; Type: FK CONSTRAINT; Schema: core; Owner: -
--

ALTER TABLE ONLY core.sessions
    ADD CONSTRAINT sessions_user_id_fkey FOREIGN KEY (user_id) REFERENCES core.users(user_id) ON DELETE CASCADE;


--
-- Name: user_assistant_configs user_assistant_configs_user_id_fkey; Type: FK CONSTRAINT; Schema: core; Owner: -
--

ALTER TABLE ONLY core.user_assistant_configs
    ADD CONSTRAINT user_assistant_configs_user_id_fkey FOREIGN KEY (user_id) REFERENCES core.users(user_id) ON DELETE CASCADE;


--
-- Name: user_gateways user_gateways_user_id_fkey; Type: FK CONSTRAINT; Schema: core; Owner: -
--

ALTER TABLE ONLY core.user_gateways
    ADD CONSTRAINT user_gateways_user_id_fkey FOREIGN KEY (user_id) REFERENCES core.users(user_id) ON DELETE CASCADE;


--
-- Name: user_mcp_configs user_mcp_configs_server_id_fkey; Type: FK CONSTRAINT; Schema: core; Owner: -
--

ALTER TABLE ONLY core.user_mcp_configs
    ADD CONSTRAINT user_mcp_configs_server_id_fkey FOREIGN KEY (server_id) REFERENCES core.mcp_servers(server_id) ON DELETE CASCADE;


--
-- Name: user_mcp_configs user_mcp_configs_user_id_fkey; Type: FK CONSTRAINT; Schema: core; Owner: -
--

ALTER TABLE ONLY core.user_mcp_configs
    ADD CONSTRAINT user_mcp_configs_user_id_fkey FOREIGN KEY (user_id) REFERENCES core.users(user_id) ON DELETE CASCADE;


--
-- PostgreSQL database dump complete
--

\unrestrict jTFDbudA7ef2gkmdwecgwFD5pv5BHl6qXuHcbMCt5hsWotpqbQQP5F0GzYHjjvS

