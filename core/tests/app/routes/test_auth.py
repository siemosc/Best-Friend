"""Acceptance-сценарии маршрутов /auth: me, login, bind, logout, смена пароля."""

from tests.app.routes.fakes import (
    AUTH_COOKIE_NAME,
    EXPECTED_USER_FIELDS,
    RuntimeFake,
    login_admin,
    make_client,
    make_profile,
)


def test_auth_login_sets_cookie() -> None:
    runtime = RuntimeFake()
    client = make_client(runtime)
    with client:
        response = client.post(
            "/auth/login",
            json={"login": "alice", "password": "correct123"},
        )
    assert response.status_code == 200
    assert AUTH_COOKIE_NAME in response.cookies
    assert runtime.auth_service.login_calls == [("alice", "correct123")]


def test_auth_login_wrong_creds_401() -> None:
    runtime = RuntimeFake()
    client = make_client(runtime)
    with client:
        response = client.post(
            "/auth/login",
            json={"login": "alice", "password": "wrongpass"},
        )
    assert response.status_code == 401
    assert response.json()["error_code"] == "AUTH_INVALID_CREDENTIALS"


def test_auth_bind_sets_cookie() -> None:
    runtime = RuntimeFake()
    client = make_client(runtime)
    with client:
        response = client.post(
            "/auth/bind",
            json={"code": "123456", "login": "bob", "password": "strongpass"},
        )
    assert response.status_code == 200
    assert AUTH_COOKIE_NAME in response.cookies
    assert runtime.auth_service.bind_calls[0]["code"] == "123456"


def test_auth_logout_clears_cookie() -> None:
    runtime = RuntimeFake()
    client = make_client(runtime)
    login_admin(runtime, client)
    with client:
        response = client.post("/auth/logout")
    assert response.status_code == 204
    assert len(runtime.auth_service.logout_calls) == 1


def test_auth_change_password_204() -> None:
    runtime = RuntimeFake()
    client = make_client(runtime)
    user = login_admin(runtime, client)
    with client:
        response = client.post(
            "/auth/change-password",
            json={"current_password": "oldpw", "new_password": "newpass1"},
        )
    assert response.status_code == 204
    assert runtime.auth_service.change_pwd_calls[0]["user_id"] == user.user_id


def test_auth_me_returns_profile() -> None:
    """Валидная сессия → 200, форма == UserResponse, без утечки чувствительных полей."""
    runtime = RuntimeFake()
    client = make_client(runtime)
    cookie = runtime.auth_service.seed_session(
        make_profile(login="u1", telegram_chat_id=42)
    )
    client.cookies.set(AUTH_COOKIE_NAME, cookie)

    with client:
        response = client.get("/auth/me")

    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == EXPECTED_USER_FIELDS
    assert {"password_hash"}.isdisjoint(body.keys())
    assert body["login"] == "u1"
    assert body["telegram_chat_id"] == 42


def test_auth_me_without_cookie_is_401() -> None:
    """Без session-cookie → 401 AUTH_INVALID_SESSION."""
    client = make_client(RuntimeFake())

    with client:
        response = client.get("/auth/me")

    assert response.status_code == 401
    assert response.json()["error_code"] == "AUTH_INVALID_SESSION"
