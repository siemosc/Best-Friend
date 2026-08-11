-- Миграция 009: STM «тупое ядро» — лента ходов из сериализованных langchain-сообщений.
--
-- Одна строка = один turn. Хранит цепочку хода как messages_to_dict (1-в-1 BaseMessage):
--   user_message — [HumanMessage], react_loop — [AI(tool_calls), ToolMessage, ...] ([] без тулов),
--   ai_message — [AIMessage] (доставленное на UI). Резка целыми ходами по token_count_full.
--
-- Сносит парную/дайджест-схему. Прода/диалогов нет → безопасный drop. _migrations не трогаем,
-- 001 не перекатывается → core.models (вручную восстановленные конфиги) не затрагивается.

DROP TABLE IF EXISTS core.stm CASCADE;

CREATE TABLE core.stm (
    id                bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id           uuid NOT NULL REFERENCES core.users(user_id) ON DELETE CASCADE,
    request_id        text NOT NULL,
    user_message      jsonb NOT NULL,
    react_loop        jsonb NOT NULL,
    ai_message        jsonb NOT NULL,
    token_count_full  integer NOT NULL,
    token_count_loop  integer NOT NULL,
    created_at        timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT uq_stm_user_request UNIQUE (user_id, request_id)
);

CREATE INDEX idx_stm_user_id ON core.stm USING btree (user_id, id);
