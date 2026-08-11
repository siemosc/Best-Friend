"""Общий guard фоновых писателей памяти: per-user asyncio.Lock.

Один реестр на процесс (core — модульный монолит, единственный писатель):
Observer и sleep-time не гоняются между собой за заметки одного пользователя.
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID


class MemoryLocks:
    """Реестр per-user локов с атомарным non-blocking захватом.

    Поверх asyncio.Lock ведётся счётчик claims (держатель + все ожидающие):
    голый `lock.locked()` не видит очередь — у asyncio.Lock acquire() на
    «свободном» локе с живыми waiters всё равно встаёт в очередь, и try-захват
    повис бы за ожидающим hold(). Проверка claims == 0 гарантирует, что
    acquire() завершится синхронно, без точки ожидания.
    """

    __slots__ = ("_claims", "_locks")

    def __init__(self) -> None:
        self._locks: dict[UUID, asyncio.Lock] = {}
        self._claims: dict[UUID, int] = {}

    def _lock_of(self, user_id: UUID) -> asyncio.Lock:
        return self._locks.setdefault(user_id, asyncio.Lock())

    @asynccontextmanager
    async def try_hold(self, user_id: UUID) -> AsyncIterator[bool]:
        """Захват без ожидания: True — лок наш, False — занят или есть очередь.

        Никогда не ждёт: конкурентный вызов не встаёт в очередь ни за
        держателем, ни за ожидающим blocking-hold.
        """
        if self._claims.get(user_id, 0) > 0:
            yield False
            return
        # claims == 0 → лок свободен и очереди нет: acquire синхронный,
        # между проверкой и захватом нет yield-точки (один event loop).
        self._claims[user_id] = 1
        try:
            await self._lock_of(user_id).acquire()
            try:
                yield True
            finally:
                self._lock_of(user_id).release()
        finally:
            self._release_claim(user_id)

    @asynccontextmanager
    async def hold(self, user_id: UUID) -> AsyncIterator[None]:
        """Blocking-захват: ждёт освобождения (sleep-time не торопится)."""
        self._claims[user_id] = self._claims.get(user_id, 0) + 1
        try:
            async with self._lock_of(user_id):
                yield
        finally:
            self._release_claim(user_id)

    def _release_claim(self, user_id: UUID) -> None:
        remaining = self._claims.get(user_id, 0) - 1
        if remaining > 0:
            self._claims[user_id] = remaining
        else:
            self._claims.pop(user_id, None)
