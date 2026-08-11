"""Настройки транскрипции речи для core monolith."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class SttSettings(BaseSettings):
    """Runtime-настройки STT: адрес сервера, модель, таймаут, лимит длительности."""

    # Origin OpenAI-совместимого сервера БЕЗ /v1 (например http://ded:8001) — путь
    # эндпоинта дописывает адаптер. Пустая строка = STT выключен.
    stt_url: str = ""
    stt_model: str = "Qwen/Qwen3-ASR-1.7B"
    stt_timeout_s: float = 60.0
    # Гейт длительности аудио: проверяется по метаданным до скачивания файла.
    stt_max_duration_s: int = 300

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )
