-- Миграция 014: ручные операции памяти из web UI — провенанс pipeline='ui'.
--
-- Вкладка «Память» правит заметки руками пользователя: правки должны быть
-- отличимы в ops-логе от агентских (tool) и фоновых (observer/sleep).
-- Новые op: delete — hard delete заметки (строка лога переживает заметку,
-- note_id уходит в NULL по FK); edit — правка флагов/субъекта без замены контента.
-- Строго аддитивная: только расширение CHECK-перечней memory_ops.

ALTER TABLE core.memory_ops DROP CONSTRAINT memory_ops_pipeline_check;
ALTER TABLE core.memory_ops ADD CONSTRAINT memory_ops_pipeline_check
    CHECK (pipeline IN ('observer', 'reconciler', 'reflector', 'tool', 'sleep', 'ui'));

ALTER TABLE core.memory_ops DROP CONSTRAINT memory_ops_op_check;
ALTER TABLE core.memory_ops ADD CONSTRAINT memory_ops_op_check
    CHECK (op IN ('add', 'supersede', 'noop', 'contradict', 'evict', 'reflect', 'pin', 'unpin', 'demote', 'revise', 'merge', 'delete', 'edit'));
