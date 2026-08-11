"""Доменные ошибки HTTP-фасада памяти."""

from bestfiend.memory.errors import MemoryDomainError


class MemoryFacadeError(MemoryDomainError):
    """Базовая ошибка фасада памяти."""

    error_code = "MEMORY_API_ERROR"
    status_code = 500

    def __init__(self, message: str) -> None:
        super().__init__(message)


class NoteNotFoundError(MemoryFacadeError):
    """Заметка по note_id не найдена у этого пользователя."""

    error_code = "NOTE_NOT_FOUND"
    status_code = 404


class NoteNotActiveError(MemoryFacadeError):
    """Правка superseded/contradicted заметки запрещена (read-only + delete)."""

    error_code = "NOTE_NOT_ACTIVE"
    status_code = 409


class SubjectNotEditableError(MemoryFacadeError):
    """Субъект правится только у fact/observation — у остальных он прибит инвариантом."""

    error_code = "SUBJECT_NOT_EDITABLE"
    status_code = 422


class PinSectionRequiredError(MemoryFacadeError):
    """pin=true требует pin_section."""

    error_code = "PIN_SECTION_REQUIRED"
    status_code = 422
