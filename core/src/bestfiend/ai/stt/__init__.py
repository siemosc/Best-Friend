"""Слой транскрипции речи (STT).

`SpeechTranscriber` — порт для потребителей (телеграм-бот принимает его и не знает
про HTTP), `OpenAICompatibleSpeechTranscriber` — адаптер к OpenAI-совместимому
`/v1/audio/transcriptions` (self-hosted vLLM), `SttSettings` — env-конфиг, который
читает только сборка приложения.
"""

from bestfiend.ai.stt.client import OpenAICompatibleSpeechTranscriber
from bestfiend.ai.stt.contracts import SpeechTranscriber
from bestfiend.ai.stt.settings import SttSettings


__all__ = [
    "OpenAICompatibleSpeechTranscriber",
    "SpeechTranscriber",
    "SttSettings",
]
