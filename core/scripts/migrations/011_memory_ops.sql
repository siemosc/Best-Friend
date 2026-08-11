-- Миграция 011: ops-лог памяти — дебаг-журнал «почему агент это запомнил/забыл».
--
-- План V2: docs/memory_rebuild_v2_plan.md. Каждая операция над заметками
-- (решения Reconciler'а, вытеснения, консолидация Reflector'а, демоция профиля,
-- tool-правки) оставляет строку — запрос вместо археологии по состоянию notes.
-- Строго аддитивная: только новая таблица, core.models не затрагивается.

CREATE TABLE core.memory_ops (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id         uuid NOT NULL REFERENCES core.users(user_id) ON DELETE CASCADE,
    pipeline        text NOT NULL CHECK (pipeline IN ('observer', 'reconciler', 'reflector', 'tool')),
    op              text NOT NULL CHECK (op IN ('add', 'supersede', 'noop', 'contradict', 'evict', 'reflect', 'pin', 'unpin', 'demote', 'revise')),
    -- Заметка-результат операции (NULL для noop — кандидат не был вставлен).
    note_id         uuid REFERENCES core.notes(id) ON DELETE SET NULL,
    -- Вторая сторона операции: заменённая/опровергнутая заметка.
    target_note_id  uuid REFERENCES core.notes(id) ON DELETE SET NULL,
    -- Краткий человекочитаемый контекст решения.
    detail          text,
    created_at      timestamp with time zone DEFAULT now() NOT NULL
);

CREATE INDEX idx_memory_ops_user ON core.memory_ops (user_id, id DESC);
