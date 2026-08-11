"""Acceptance-сценарии маршрутов /users/{id}/assistant-config."""

from tests.app.routes.fakes import (
    AUTH_COOKIE_NAME,
    RuntimeFake,
    login_admin,
    make_client,
    make_profile,
)


def test_get_assistant_config_self_or_admin_allowed() -> None:
    runtime = RuntimeFake()
    client = make_client(runtime)
    user = make_profile(role="user")
    runtime.user_service.seed(user)
    cookie = runtime.auth_service.seed_session(user)
    client.cookies.set(AUTH_COOKIE_NAME, cookie)
    with client:
        response = client.get(f"/users/{user.user_id}/assistant-config")
    assert response.status_code == 200
    assert response.json()["user_id"] == str(user.user_id)


def test_get_assistant_config_other_user_forbidden() -> None:
    runtime = RuntimeFake()
    client = make_client(runtime)
    user = make_profile(role="user")
    other = make_profile(role="user")
    runtime.user_service.seed(user)
    runtime.user_service.seed(other)
    cookie = runtime.auth_service.seed_session(user)
    client.cookies.set(AUTH_COOKIE_NAME, cookie)
    with client:
        response = client.get(f"/users/{other.user_id}/assistant-config")
    assert response.status_code == 403


def test_patch_assistant_config_accepts_free_llm_custom_config() -> None:
    """ALLOW-ALL: llm_custom_config — свободный jsonb (api_key и т.п. допустимы)."""
    runtime = RuntimeFake()
    client = make_client(runtime)
    user = make_profile(role="user")
    runtime.user_service.seed(user)
    cookie = runtime.auth_service.seed_session(user)
    client.cookies.set(AUTH_COOKIE_NAME, cookie)
    with client:
        response = client.patch(
            f"/users/{user.user_id}/assistant-config",
            json={
                "user_instruction": "be terse",
                "llm_custom_config": {
                    "provider": "openrouter",
                    "model": "x",
                    "api_key": "sk-user",
                },
            },
        )
    assert response.status_code == 200
    assert runtime.assistant_service.update_calls
    _, fields = runtime.assistant_service.update_calls[-1]
    assert fields["llm_custom_config"]["api_key"] == "sk-user"


def test_post_assistant_config_reset() -> None:
    runtime = RuntimeFake()
    client = make_client(runtime)
    login_admin(runtime, client)
    target = make_profile(role="user")
    runtime.user_service.seed(target)
    with client:
        response = client.post(
            f"/users/{target.user_id}/assistant-config/reset",
        )
    assert response.status_code == 200
    assert runtime.assistant_service.reset_calls == [target.user_id]
