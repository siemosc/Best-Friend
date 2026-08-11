"""Acceptance-сценарии маршрута /dashboard/health."""

from tests.app.routes.fakes import (
    RuntimeFake,
    login_admin,
    make_client,
)


def test_dashboard_health_returns_snapshot() -> None:
    runtime = RuntimeFake()
    client = make_client(runtime)
    login_admin(runtime, client)
    with client:
        response = client.get("/dashboard/health")
    assert response.status_code == 200
    body = response.json()
    assert "services" in body
    assert "links" in body
    assert body["services"][0]["name"] == "core"


def test_dashboard_health_without_session_is_401() -> None:
    runtime = RuntimeFake()
    client = make_client(runtime)
    with client:
        response = client.get("/dashboard/health")
    assert response.status_code == 401
