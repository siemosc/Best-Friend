-- Миграция 010: лог-центричная память — turns (бывш. stm) + notes/entities/watermarks.
--
-- Архитектура: docs/memory_architecture_alt.md, план V1: docs/memory_rebuild_v1_plan.md.
-- Лог (turns) — источник истины; notes — атомарные заметки Observer'а с тегами сущностей;
-- журнал/профиль — выборки notes по флагам (in_journal/pinned). Поля status/superseded_by/
-- pinned/pipeline_ver заложены под V2 (Reconciler/промоушен) — V1 их не управляет.
--
-- Строго аддитивная: core.models (вручную восстановленные конфиги) НЕ затрагивается,
-- снапшот 001 не перекатывается. Данные core.stm не нужны (подтверждено) → безопасный drop.

-- SCHEMA public явно: search_path миграций = core,public → без указания расширение
-- встало бы в core, а pgvector-кодек (register_vector) ищет тип vector в public.
CREATE EXTENSION IF NOT EXISTS vector SCHEMA public;
CREATE EXTENSION IF NOT EXISTS pg_trgm SCHEMA public;

-- ── Лог: переименование stm → turns (та же структура, данные дропаются) ──

DROP TABLE IF EXISTS core.stm CASCADE;

CREATE TABLE core.turns (
    id                bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id           uuid NOT NULL REFERENCES core.users(user_id) ON DELETE CASCADE,
    request_id        text NOT NULL,
    user_message      jsonb NOT NULL,
    react_loop        jsonb NOT NULL,
    ai_message        jsonb NOT NULL,
    token_count_full  integer NOT NULL,
    token_count_loop  integer NOT NULL,
    created_at        timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT uq_turns_user_request UNIQUE (user_id, request_id)
);

CREATE INDEX idx_turns_user_id ON core.turns USING btree (user_id, id);

-- ── Реестр сущностей ──

CREATE TABLE core.entities (
    id              uuid PRIMARY KEY,
    user_id         uuid NOT NULL REFERENCES core.users(user_id) ON DELETE CASCADE,
    canonical_name  text NOT NULL,
    embedding       vector(1024),
    created_at      timestamp with time zone DEFAULT now() NOT NULL
);

-- Уникальность каноничного имени per user — expression-индекс (lower в constraint невалиден).
CREATE UNIQUE INDEX uq_entities_user_name ON core.entities (user_id, lower(canonical_name));

CREATE TABLE core.entity_aliases (
    entity_id  uuid NOT NULL REFERENCES core.entities(id) ON DELETE CASCADE,
    alias      text NOT NULL
);

CREATE UNIQUE INDEX uq_entity_aliases ON core.entity_aliases (entity_id, lower(alias));
-- trgm — толерантный матч упоминаний (опечатки/транслит) на read path.
CREATE INDEX idx_entity_aliases_trgm ON core.entity_aliases USING gin (alias gin_trgm_ops);
CREATE INDEX idx_entity_aliases_entity ON core.entity_aliases (entity_id);

-- ── Заметки: единственный атом памяти ──

CREATE TABLE core.notes (
    id                 uuid PRIMARY KEY,
    user_id            uuid NOT NULL REFERENCES core.users(user_id) ON DELETE CASCADE,
    kind               text NOT NULL CHECK (kind IN ('observation', 'fact', 'preference', 'rule', 'reflection')),
    content            text NOT NULL,
    -- Bitemporal: event_time — когда было истинно в мире (из содержания, может отсутствовать);
    -- observed_at — когда записала система.
    event_time         timestamp with time zone,
    observed_at        timestamp with time zone NOT NULL,
    status             text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'superseded', 'contradicted')),
    superseded_by      uuid REFERENCES core.notes(id),
    pinned             boolean NOT NULL DEFAULT false,
    pin_section        text CHECK (pin_section IN ('identity', 'preferences', 'relationships', 'rules')),
    in_journal         boolean NOT NULL DEFAULT false,
    journal_weight     smallint NOT NULL DEFAULT 1,  -- 2=high, 1=mid, 0=low: порядок вытеснения
    -- Провенанс: span ходов лога, из которых заметка извлечена.
    source_turn_start  bigint,
    source_turn_end    bigint,
    embedding          vector(1024),
    content_tsv        tsvector GENERATED ALWAYS AS (to_tsvector('russian', content)) STORED,
    use_count          integer NOT NULL DEFAULT 0,
    pipeline_ver       integer NOT NULL DEFAULT 1,
    created_at         timestamp with time zone DEFAULT now() NOT NULL
);

CREATE INDEX idx_notes_user_journal ON core.notes (user_id, journal_weight, observed_at) WHERE in_journal;
CREATE INDEX idx_notes_user_pinned ON core.notes (user_id) WHERE pinned;
CREATE INDEX idx_notes_user_active ON core.notes (user_id) WHERE status = 'active';
CREATE INDEX idx_notes_tsv ON core.notes USING gin (content_tsv);
-- Vector-индекс сознательно не создаём: exact scan на объёмах личного чата быстрее
-- и точнее; ANN (HNSW) — отложенный upgrade при росте.

CREATE TABLE core.note_entities (
    note_id    uuid NOT NULL REFERENCES core.notes(id) ON DELETE CASCADE,
    entity_id  uuid NOT NULL REFERENCES core.entities(id) ON DELETE CASCADE,
    PRIMARY KEY (note_id, entity_id)
);

CREATE INDEX idx_note_entities_entity ON core.note_entities (entity_id);

-- ── Watermarks: идемпотентность фоновых пайплайнов ──

CREATE TABLE core.memory_watermarks (
    user_id       uuid NOT NULL REFERENCES core.users(user_id) ON DELETE CASCADE,
    pipeline      text NOT NULL,
    last_turn_id  bigint NOT NULL DEFAULT 0,
    updated_at    timestamp with time zone DEFAULT now() NOT NULL,
    PRIMARY KEY (user_id, pipeline)
);
