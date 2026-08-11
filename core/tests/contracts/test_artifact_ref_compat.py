"""ArtifactRef: backward-compat имён/ключей + вычисление artifact_llm_name."""

from bestfiend.contracts.artifacts import ArtifactRef


# uuid7-подобный id с предсказуемым хвостом для проверки artifact_llm_name.
_ID = "0190aaaabbbbccccdddd1234567890ab"  # последние 6 символов → "7890ab"


def test_legacy_semantic_name_maps_to_artifact_user_name() -> None:
    """Старый payload с semantic_name читается в artifact_user_name."""
    ref = ArtifactRef.model_validate(
        {
            "artifact_id": "a1",
            "type": "document",
            "semantic_name": "old-report",
            "description": "d",
            "path": "u/src/id/",
        }
    )
    assert ref.artifact_user_name == "old-report"


def test_legacy_filename_maps_to_artifact_user_name() -> None:
    """Промежуточный payload с filename тоже читается в artifact_user_name."""
    ref = ArtifactRef.model_validate(
        {
            "artifact_id": "a2",
            "type": "document",
            "filename": "new-report.md",
            "description": "d",
            "path": "u/src/id/",
        }
    )
    assert ref.artifact_user_name == "new-report.md"


def test_legacy_path_without_storage_key_deserializes() -> None:
    """Старая запись с path и без storage_key читается без ошибки; ключ пустой."""
    ref = ArtifactRef.model_validate(
        {
            "artifact_id": "a3",
            "type": "document",
            "filename": "f",
            "path": "u/src/id/",
        }
    )
    assert ref.storage_key == ""


def test_construct_by_field_name() -> None:
    """Конструирование по именам полей (populate_by_name)."""
    ref = ArtifactRef(
        artifact_id="a4",
        type="image",
        artifact_user_name="photo.png",
        storage_key="u/id/data",
    )
    assert ref.artifact_user_name == "photo.png"
    assert ref.storage_key == "u/id/data"


def test_serialization_uses_new_field_names() -> None:
    """model_dump пишет artifact_user_name/storage_key, не старые ключи."""
    ref = ArtifactRef(
        artifact_id="a5",
        type="document",
        artifact_user_name="r.md",
        storage_key="u/id/data",
    )
    dumped = ref.model_dump(mode="json")
    assert "artifact_user_name" in dumped
    assert "storage_key" in dumped
    assert "filename" not in dumped
    assert "path" not in dumped


def test_artifact_llm_name_with_extension() -> None:
    """С расширением: {stem}_{id[-6:]}{ext}."""
    ref = ArtifactRef(artifact_id=_ID, type="document", artifact_user_name="report.md")
    assert ref.artifact_llm_name == "report_7890ab.md"


def test_artifact_llm_name_without_extension() -> None:
    """Без расширения — без хвостовой точки."""
    ref = ArtifactRef(artifact_id=_ID, type="document", artifact_user_name="report")
    assert ref.artifact_llm_name == "report_7890ab"


def test_artifact_llm_name_keeps_cyrillic() -> None:
    """Кириллица в имени сохраняется как есть."""
    ref = ArtifactRef(artifact_id=_ID, type="image", artifact_user_name="Отчёт.png")
    assert ref.artifact_llm_name == "Отчёт_7890ab.png"
