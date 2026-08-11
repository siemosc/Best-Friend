"""Общие списки колонок таблицы ``notes`` для SQL-запросов памяти."""

NOTE_COLUMNS = (
    "id, user_id, kind, subject, content, event_time, observed_at, status, pinned, "
    "pin_section, in_journal, journal_weight, source_turn_start, source_turn_end, "
    "use_count"
)

NOTE_COLUMNS_N = (
    "n.id, n.user_id, n.kind, n.subject, n.content, n.event_time, n.observed_at, "
    "n.status, n.pinned, n.pin_section, n.in_journal, n.journal_weight, "
    "n.source_turn_start, n.source_turn_end, n.use_count"
)
