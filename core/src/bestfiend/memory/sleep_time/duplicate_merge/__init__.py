"""Слияние дублирующих заметок во время простоя."""

from bestfiend.memory.sleep_time.duplicate_merge.service import run_duplicate_merge


__all__ = ["run_duplicate_merge"]
