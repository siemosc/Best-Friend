"""Разбор списка пользователей, которым разрешён доступ к Telegram-боту."""


def parse_allowed_user_ids(
    raw: str | list[str] | list[int] | None,
) -> list[int] | None:
    """Парсит список разрешённых user_id из env или конфигурации."""
    if raw is None:
        return None
    if isinstance(raw, list):
        return _parse_user_id_list(raw)
    if isinstance(raw, str):
        return _parse_user_id_text(raw)
    user_id = _parse_user_id(raw)
    return [user_id] if user_id is not None else None


def _parse_user_id_list(raw: list[str] | list[int]) -> list[int] | None:
    parsed = [_parse_user_id(item) for item in raw]
    user_ids = [user_id for user_id in parsed if user_id is not None]
    if len(user_ids) == len(parsed):
        return user_ids
    return user_ids or None


def _parse_user_id_text(raw: str) -> list[int] | None:
    parts = [part.strip() for part in raw.split(",") if part.strip()]
    if not parts:
        return None
    return [int(part) for part in parts]


def _parse_user_id(raw: object) -> int | None:
    try:
        return int(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
