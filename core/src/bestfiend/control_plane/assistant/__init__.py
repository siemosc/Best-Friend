"""Assistant capability — per-user конфиг ассистента (overrides + instructions).

Reader для model_registry + writer-набор (bootstrap для identity-creation,
reset/update для web-админ /api).
"""

from bestfiend.control_plane.assistant.errors import (
    AssistantConfigError,
    AssistantConfigUnavailableError,
)
from bestfiend.control_plane.assistant.models import UserAssistantConfigRecord
from bestfiend.control_plane.assistant.repository import UserAssistantConfigRepository


__all__ = [
    "AssistantConfigError",
    "AssistantConfigUnavailableError",
    "UserAssistantConfigRecord",
    "UserAssistantConfigRepository",
]
