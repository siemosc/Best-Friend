"""Лог-центричная память: append-only лог ходов — источник истины.

Notes/журнал/профиль — перестраиваемые индексы над логом. Read path без LLM
(search_pipeline: хвост + журнал + профиль + recall), write path — запись хода
(write_pipeline) с фоновыми пайплайнами: Observer → Reconciler/Reflector и
sleep-time консолидация в простое.
"""
