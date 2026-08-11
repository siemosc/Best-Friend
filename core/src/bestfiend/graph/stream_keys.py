"""Ключи custom-стрима node→runtime.

Точка правды для ключей, которыми ноды пишут в `runtime.stream_writer`.
Producer'ы (`react`, `error`) и consumer (`graph.streaming.invoke_graph`)
импортируют отсюда — consumer мапит чанки по ключу в `AnswerDelta`/`ProgressStep`
и публикует через `StreamPublisher`.
"""

ANSWER_DELTA_KEY = "answer_delta"  # видимый текст ответа → AnswerDelta
PROGRESS_STEP_KEY = (
    "progress_step"  # человекочитаемое «что я сейчас делаю» → ProgressStep
)
ANSWER_RESET_KEY = (
    "answer_reset"  # сброс накопленного финала: предыдущий сегмент был preface
)
