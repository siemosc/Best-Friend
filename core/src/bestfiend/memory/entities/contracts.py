"""Контракты реестра сущностей памяти."""

from dataclasses import dataclass, field
from uuid import UUID


@dataclass(frozen=True, slots=True)
class Entity:
    """Сущность реестра с алиасами."""

    id: UUID
    user_id: UUID
    canonical_name: str
    aliases: tuple[str, ...] = field(default=())
