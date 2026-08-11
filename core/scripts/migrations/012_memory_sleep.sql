-- Миграция 012: sleep-time слой памяти — производные kind'ы, merge-операция, автопробы.
--
-- План V3: docs/memory_rebuild_v3_plan.md. Карточки сущностей и сводки периодов —
-- те же notes с новыми kind (никаких derived_docs: recall/supersede/ops работают
-- из коробки). memory_probes — метрика качества recall (hit@k), «дашборд» = SQL.
-- Строго аддитивная: core.models не затрагивается.

-- Производные документы sleep-time входят в перечень kind.
ALTER TABLE core.notes DROP CONSTRAINT notes_kind_check;
ALTER TABLE core.notes ADD CONSTRAINT notes_kind_check
    CHECK (kind IN ('observation', 'fact', 'preference', 'rule', 'reflection', 'entity_card', 'period_summary'));

-- Sleep-пайплайн и операция слияния почти-дублей.
ALTER TABLE core.memory_ops DROP CONSTRAINT memory_ops_pipeline_check;
ALTER TABLE core.memory_ops ADD CONSTRAINT memory_ops_pipeline_check
    CHECK (pipeline IN ('observer', 'reconciler', 'reflector', 'tool', 'sleep'));

ALTER TABLE core.memory_ops DROP CONSTRAINT memory_ops_op_check;
ALTER TABLE core.memory_ops ADD CONSTRAINT memory_ops_op_check
    CHECK (op IN ('add', 'supersede', 'noop', 'contradict', 'evict', 'reflect', 'pin', 'unpin', 'demote', 'revise', 'merge'));

-- ── Автопробы: вопрос с известным ответом → боевой recall → hit/rank ──

CREATE TABLE core.memory_probes (
    id                bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id           uuid NOT NULL REFERENCES core.users(user_id) ON DELETE CASCADE,
    question          text NOT NULL,
    expected_note_id  uuid REFERENCES core.notes(id) ON DELETE SET NULL,
    hit               boolean NOT NULL,
    -- Позиция ожидаемой заметки в выдаче recall; NULL = miss.
    rank              integer,
    created_at        timestamp with time zone DEFAULT now() NOT NULL
);

CREATE INDEX idx_memory_probes_user ON core.memory_probes (user_id, created_at DESC);
