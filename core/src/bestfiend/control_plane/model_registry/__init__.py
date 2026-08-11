"""Model registry — graph-facing reader-capability для резолва модели.

Резолвит LLM-конфиг единой модели графа + per-user llm_custom_config/instruction +
user_environment (timezone/city/country).
"""

from bestfiend.control_plane.model_registry.contracts import (
    ResolveModelRequest,
    ResolveModelResponse,
)
from bestfiend.control_plane.model_registry.errors import (
    ModelNotFoundError,
    ModelRegistryError,
)
from bestfiend.control_plane.model_registry.models import ModelConfigRecord
from bestfiend.control_plane.model_registry.service import ModelRegistry


__all__ = [
    "ModelConfigRecord",
    "ModelNotFoundError",
    "ModelRegistry",
    "ModelRegistryError",
    "ResolveModelRequest",
    "ResolveModelResponse",
]
