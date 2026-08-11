"""Тексты error-ноды: static-отписки + finalize-промпт + хинты по виду сбоя."""

# Детерминированные отписки, когда LLM не зовём (провайдер мёртв / состояние неясно).
STATIC_TEXTS: dict[str, str] = {
    "provider_down": (
        "Сервис ответа временно недоступен — не получилось обработать запрос. "
        "Попробуй ещё раз чуть позже."
    ),
    "unexpected": (
        "При обработке запроса произошла внутренняя ошибка. "
        "Попробуй повторить или переформулировать."
    ),
}

# Мягкий нетехнический хинт «почему остановились» — идёт в finalize-промпт.
KIND_HINTS: dict[str, str] = {
    "context_exceeded": "There was too much data to fit everything at once.",
    "loop_exhausted": "The task turned out too large — it took too many steps.",
}

FINALIZE_RULES = """# Answering After an Interruption
The work stopped before completion — answer the user based on what has been gathered so far.
- State honestly that the task could not be fully completed.
- Rely only on facts from the work actually done.
- Briefly explain in your own words why you had to stop.
- Answer in the user's language; format the reply clearly."""
