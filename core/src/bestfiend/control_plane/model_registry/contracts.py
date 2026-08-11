"""Capability-interface DTO для /internal/models/resolve.

Module-local: capability-interface request/response едут со своей capability,
а не в нейтральном `bestfiend/contracts/identity.py` (там только pass-through
DTO без владеющей capability).
"""

from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from bestfiend.contracts.user_environment import UserEnvironment


class ResolveModelRequest(BaseModel):
    """Запрос резолва единой модели графа."""

    # model_id задевает protected namespace pydantic `model_` — снимаем защиту.
    model_config = ConfigDict(extra="forbid", protected_namespaces=())

    model_id: str
    user_id: UUID | None = None


class ResolveModelResponse(BaseModel):
    """Ответ резолва — passthrough LLM-конфиг единой модели + user_instruction."""

    config: dict[str, Any]
    user_instruction: str | None = None
    user_environment: UserEnvironment | None = None
