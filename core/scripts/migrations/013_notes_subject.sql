-- Миграция 013: ось subject у заметок — о ком знание (user / agent / world).
--
-- kind отвечает «как знание живёт» (журнал/архив/производное), subject — «о ком».
-- NULL = субъект не применим (производные агрегаты reflection/entity_card/
-- period_summary) или заметка записана до классификации. Инвариант
-- preference→user, rule→agent держит код на границе вставки (NotesRepository).

ALTER TABLE core.notes ADD COLUMN subject text
    CHECK (subject IN ('user', 'agent', 'world'));

-- Backfill детерминированной части: субъект preference/rule следует из kind.
UPDATE core.notes SET subject = 'user' WHERE kind = 'preference';
UPDATE core.notes SET subject = 'agent' WHERE kind = 'rule';
