"""Контракт UserEnvironment: поля и дефолты кросс-модульного окружения юзера."""

from bestfiend.contracts.user_environment import UserEnvironment


def test_user_environment_defaults_and_full() -> None:
    minimal = UserEnvironment(timezone="Europe/Belgrade")
    assert minimal.city is None
    assert minimal.country is None
    full = UserEnvironment(timezone="UTC", city="Belgrade", country="RS")
    assert (full.city, full.country) == ("Belgrade", "RS")
