-- Миграция 004: схлопывание per-slot конфига ассистента в единые поля.
--
-- Граф больше не делит на work/answer/intent/action: одна инструкция и одна
-- модель на весь граф. Заменяем 4 инструкции на user_instruction; overrides
-- (per-slot дельты) на llm_custom_config (свободный jsonb по структуре models.config,
-- непустой = полная замена дефолта). drop+add без переноса (реальных пользователей
-- нет; bootstrap пересоздаст пустые записи). Инкрементально (001-003 уже в _migrations).

ALTER TABLE core.user_assistant_configs
    ADD COLUMN user_instruction text NOT NULL DEFAULT '',
    ADD COLUMN llm_custom_config jsonb NOT NULL DEFAULT '{}'::jsonb,
    DROP COLUMN work_instruction,
    DROP COLUMN answer_instruction,
    DROP COLUMN intent_instruction,
    DROP COLUMN action_instruction,
    DROP COLUMN overrides;
