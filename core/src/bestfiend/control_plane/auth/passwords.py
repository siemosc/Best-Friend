"""Парольные хелперы control_plane: bcrypt-хеширование и проверка."""

import bcrypt


def hash_password(plain: str, *, cost: int = 12) -> str:
    """Хеширует пароль bcrypt со встроенным солением."""
    salt = bcrypt.gensalt(rounds=cost)
    hashed = bcrypt.hashpw(plain.encode("utf-8"), salt)
    return hashed.decode("utf-8")


def verify_password(plain: str, password_hash: str) -> bool:
    """Проверяет пароль против bcrypt-хеша. Невалидный hash → False (без exception)."""
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False
