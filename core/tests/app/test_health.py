"""Контракт /health: core отвечает 200 и ожидаемым телом."""

from fastapi.testclient import TestClient

from bestfiend.app.http import app


def test_health_ok() -> None:
    """GET /health → 200 {"status": "ok"}."""
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
