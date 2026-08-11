"""Acceptance-сценарии маршрутов /users: самоправка профиля и админский PATCH."""

from tests.app.routes.fakes import (
    AUTH_COOKIE_NAME,
    EXPECTED_USER_FIELDS,
    RuntimeFake,
    login_admin,
    make_client,
    make_profile,
)


def test_patch_users_me_updates_own_profile() -> None:
    runtime = RuntimeFake()
    client = make_client(runtime)
    user = make_profile(role="user", login="u1")
    runtime.user_service.seed(user)
    cookie = runtime.auth_service.seed_session(user)
    client.cookies.set(AUTH_COOKIE_NAME, cookie)
    with client:
        response = client.patch("/users/me", json={"timezone": "UTC"})
    assert response.status_code == 200
    assert response.json()["timezone"] == "UTC"
    assert runtime.user_service.last_update_own == (
        user.user_id,
        {"timezone": "UTC"},
    )


def test_patch_users_admin_changes_role() -> None:
    runtime = RuntimeFake()
    client = make_client(runtime)
    admin = login_admin(runtime, client)
    target = make_profile(role="user")
    runtime.user_service.seed(target)
    with client:
        response = client.patch(
            f"/users/{target.user_id}",
            json={"role": "admin"},
        )
    assert response.status_code == 200
    assert runtime.user_service.last_update_profile is not None
    assert runtime.user_service.last_update_profile["role"] == "admin"
    assert runtime.user_service.last_update_profile["current_user_id"] == admin.user_id


def test_patch_users_self_edit_rejected_with_self_edit_error() -> None:
    runtime = RuntimeFake()
    client = make_client(runtime)
    admin = login_admin(runtime, client)
    runtime.user_service.seed(admin)
    with client:
        response = client.patch(
            f"/users/{admin.user_id}",
            json={"role": "user"},
        )
    assert response.status_code == 400
    assert response.json()["error_code"] == "USER_SELF_EDIT_NOT_ALLOWED"


def test_list_users_returns_all_profiles() -> None:
    """Admin-сессия → 200, форма == UserResponse, нет утечки чувствительных полей."""
    runtime = RuntimeFake()
    client = make_client(runtime)
    runtime.user_service.seed(make_profile(telegram_chat_id=1, login="u1"))
    runtime.user_service.seed(make_profile(telegram_chat_id=2, login="u2"))
    login_admin(runtime, client)

    with client:
        response = client.get("/users")

    assert response.status_code == 200
    users = response.json()
    assert len(users) == 2
    assert set(users[0].keys()) == EXPECTED_USER_FIELDS
    assert {"password_hash"}.isdisjoint(users[0].keys())
    assert {1, 2} == {user["telegram_chat_id"] for user in users}


def test_list_users_without_cookie_is_401() -> None:
    """Без session-cookie → 401 AUTH_INVALID_SESSION."""
    client = make_client(RuntimeFake())

    with client:
        response = client.get("/users")

    assert response.status_code == 401
    assert response.json()["error_code"] == "AUTH_INVALID_SESSION"


def test_list_users_with_non_admin_cookie_is_403() -> None:
    """Валидная сессия обычного юзера → 403 AUTH_FORBIDDEN."""
    runtime = RuntimeFake()
    client = make_client(runtime)
    cookie = runtime.auth_service.seed_session(make_profile(role="user", login="u"))
    client.cookies.set(AUTH_COOKIE_NAME, cookie)

    with client:
        response = client.get("/users")

    assert response.status_code == 403
    assert response.json()["error_code"] == "AUTH_FORBIDDEN"


def test_list_users_empty() -> None:
    """Admin-сессия, пустой список юзеров → 200 []."""
    runtime = RuntimeFake()
    client = make_client(runtime)
    login_admin(runtime, client)

    with client:
        response = client.get("/users")

    assert response.status_code == 200
    assert response.json() == []
