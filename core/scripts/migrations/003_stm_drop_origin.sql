-- Миграция 003: выпил origin/origin_source из stm.
--
-- В новом ядре источник/режим хода несёт processing_mode; origin/origin_source —
-- старые аргументы, удалены по всему пайпу (граф/ingress их больше не имеют).
-- Колонки ничем не заполняются — дропаем. Инкрементально (001/002 уже в _migrations).

ALTER TABLE core.stm DROP COLUMN origin;
ALTER TABLE core.stm DROP COLUMN origin_source;
