"""Кросс-модульный контракт артефакта: ArtifactRef — нейтральный дескриптор для передачи по системе."""

from pathlib import PurePosixPath
from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field


class ArtifactRef(BaseModel):
    """Нейтральный дескриптор артефакта для передачи по системе."""

    model_config = ConfigDict(populate_by_name=True)

    artifact_id: str = Field(min_length=1)
    artifact_user_name: str = Field(
        default="",
        validation_alias=AliasChoices(
            "artifact_user_name", "filename", "semantic_name"
        ),
        description=(
            "Имя файла, как его задал источник (отдаётся юзеру); alias держит "
            "старые STM-записи (filename / semantic_name)."
        ),
    )
    type: str = Field(min_length=1)
    description: str = ""
    storage_key: str = Field(
        default="",
        description=(
            "Полный ключ к data-объекту: {user_id}/{artifact_id}/data. "
            "meta.json лежит по соседству (тот же префикс + meta.json). "
            "default='' держит старые STM-записи (поле path)."
        ),
    )
    art_meta: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Свободная структура type-specific полей (passthrough в meta.json); "
            "forward-compat, бизнес-читателя нет."
        ),
    )

    @property
    def artifact_llm_name(self) -> str:
        """Имя для LLM: {stem}_{последние 6 символов artifact_id}{ext}.

        stem/ext берутся из artifact_user_name. Без расширения — без хвостовой
        точки; кириллица сохраняется как есть.
        """
        name = PurePosixPath(self.artifact_user_name)
        return f"{name.stem}_{self.artifact_id[-6:]}{name.suffix}"
