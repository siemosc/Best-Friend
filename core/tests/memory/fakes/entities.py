"""Тестовые двойники реестра сущностей памяти."""

from uuid import UUID, uuid4

from bestfiend.memory.entities.contracts import Entity


class EntityRepositoryFake:
    """Реестр с настраиваемым разрешением имён и журналом созданных сущностей."""

    def __init__(self, known: dict[str, UUID] | None = None) -> None:
        self.known = known or {}
        self.created: list[str] = []

    async def list_entities(self, user_id: UUID) -> list[Entity]:
        return [
            Entity(id=entity_id, user_id=user_id, canonical_name=name, aliases=(name,))
            for name, entity_id in self.known.items()
        ]

    async def resolve_names(self, user_id: UUID, names: list[str]) -> dict[str, UUID]:
        known_by_casefold = {
            known_name.casefold(): entity_id
            for known_name, entity_id in self.known.items()
        }
        return {
            name: known_by_casefold[name.casefold()]
            for name in names
            if name.casefold() in known_by_casefold
        }

    async def create_entity(self, user_id: UUID, canonical_name: str) -> UUID:
        entity_id = uuid4()
        self.known[canonical_name] = entity_id
        self.created.append(canonical_name)
        return entity_id

    async def canonical_name_of(self, entity_id: UUID) -> str | None:
        return next(
            (
                name
                for name, known_entity_id in self.known.items()
                if known_entity_id == entity_id
            ),
            None,
        )
