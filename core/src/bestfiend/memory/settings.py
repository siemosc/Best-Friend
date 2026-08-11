"""Настройки сервиса memory."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class MemorySettings(BaseSettings):
    """Конфигурация памяти: модели, бюджеты контекста, пороги пайплайнов.

    Пустой model id = соответствующая способность сознательно выключена
    (observer без LLM, recall без векторной ветки).
    """

    # ── Модели (id в таблице core.models; прямой lookup, без per-user resolve) ──
    memory_llm_model_id: str = "deepseek/deepseek-v4-flash-nothink"
    memory_embedding_model_id: str = "openrouter/qwen3-embedding-8b"
    # MRL-усечение Qwen3 (4096 → 1024) + L2-normalize на нашей стороне.
    memory_embedding_dim: int = 1024

    # ── Write-path бюджеты (фоновые пайплайны; не read-раскладка) ──
    # Журнал сворачивается Reflector'ом при переполнении этого порога.
    journal_token_budget: int = 4_000
    # Потолок одной секции профиля; переполнение → демоция наименее используемых.
    profile_section_token_budget: int = 700

    # ── Read-раскладка контекста (доли окна модели графа; см. plan_read_budget) ──
    # Fallback'и, когда в config jsonb модели нет context_window / max_tokens.
    ctx_default_window: int = 32_000
    ctx_default_reserve: int = 4_000
    # Каждый блок памяти — доля working-окна, зажатая [floor … cap]:
    # cap гасит раздувание на большом окне, floor держит блок живым на малом.
    ctx_journal_pct: float = 0.08
    ctx_journal_floor: int = 1_500
    ctx_journal_cap: int = 8_000
    ctx_profile_pct: float = 0.03
    ctx_profile_floor: int = 800
    ctx_profile_cap: int = 3_000
    ctx_recall_pct: float = 0.06
    ctx_recall_floor: int = 1_000
    ctx_recall_cap: int = 6_000
    # Сырая STM (хвост лога) — остаток окна, но под потолком (lost-in-the-middle).
    ctx_log_tail_floor: int = 6_000
    ctx_log_tail_cap: int = 40_000

    # ── Observer ──
    # Порог необработанных токенов лога (после watermark), при котором запускается прогон.
    observer_token_threshold: int = 6_000
    # Потолок ходов на один прогон — страховка от гигантского батча при ретро-прогоне.
    observer_max_turns: int = 30

    # ── Reconciler ──
    # Сколько соседей по памяти показывается LLM на каждого кандидата.
    reconciler_neighbors_k: int = 5

    # ── Recall ──
    recall_candidates_per_branch: int = 50
    recall_top_k: int = 8
    # Gate: блок вставляется при max cosine ≥ порога ИЛИ entity-hit ИЛИ time-hit.
    recall_min_similarity: float = 0.5

    # ── Sleep-time (консолидация в простое) ──
    # Минуты тишины после последнего хода до старта цикла.
    sleep_idle_minutes: int = 30
    sleep_max_cards_per_cycle: int = 3
    # Горячая сущность: минимум активных заметок с тегом для карточки.
    sleep_entity_hot_threshold: int = 5
    sleep_card_source_notes_max: int = 50
    sleep_max_summaries_per_cycle: int = 1
    sleep_summary_min_notes: int = 5
    sleep_summary_weeks_back: int = 4
    sleep_merge_similarity: float = 0.92
    sleep_merge_max_pairs: int = 5
    sleep_max_probes_per_cycle: int = 2
    sleep_probe_recent_days: int = 7

    # ── Лог ──
    log_max_fetch_turns: int = 500
    # Потолок ходов одного memory_read_log-вызова.
    read_log_max_turns: int = 50

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )
