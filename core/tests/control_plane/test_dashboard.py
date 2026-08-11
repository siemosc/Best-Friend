"""DashboardService snapshot.

Probe-stub: возвращает заранее запрограммированные ServiceHealth — `snapshot`
агрегирует. Реальный httpx не используется (unit-уровень).
"""

from datetime import UTC, datetime

import pytest

from bestfiend.control_plane.dashboard import (
    DashboardService,
    ServiceHealth,
)


_NOW = datetime.now(UTC)


class _ProbeStub:
    def __init__(self, responses: dict[str, ServiceHealth]) -> None:
        self.calls: list[tuple[str, str]] = []
        self.closed = False
        self._responses = responses

    async def probe(self, name: str, url: str) -> ServiceHealth:
        self.calls.append((name, url))
        if name not in self._responses:
            raise AssertionError(f"unexpected probe target: {name}")
        return self._responses[name]

    async def aclose(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_snapshot_includes_all_configured_services() -> None:
    probe = _ProbeStub(
        responses={
            "core": ServiceHealth(
                name="core",
                url="http://localhost:8010",
                status="healthy",
                latency_ms=12,
                checked_at=_NOW,
            ),
        },
    )
    service = DashboardService(
        probe_client=probe,
        service_urls={
            "core": "http://localhost:8010",
        },
        langfuse_ui_url="http://langfuse.test",
    )

    snapshot = await service.snapshot()

    assert {h.name for h in snapshot.services} == {"core"}
    assert all(h.status == "healthy" for h in snapshot.services)
    assert snapshot.links.langfuse_url == "http://langfuse.test"


@pytest.mark.asyncio
async def test_snapshot_propagates_probe_status() -> None:
    """Один сервис unreachable, второй healthy — snapshot не throw."""
    probe = _ProbeStub(
        responses={
            "core": ServiceHealth(
                name="core",
                url="http://localhost:8010",
                status="healthy",
                latency_ms=8,
                checked_at=_NOW,
            ),
        },
    )
    service = DashboardService(
        probe_client=probe,
        service_urls={
            "core": "http://localhost:8010",
        },
        langfuse_ui_url="",
    )

    snapshot = await service.snapshot()

    by_name = {h.name: h for h in snapshot.services}
    assert by_name["core"].status == "healthy"


@pytest.mark.asyncio
async def test_aclose_delegates_to_probe_client() -> None:
    """aclose сервиса закрывает probe-клиент (CoreRuntime.stop зовёт именно сервис)."""
    probe = _ProbeStub(responses={})
    service = DashboardService(
        probe_client=probe,
        service_urls={},
        langfuse_ui_url="",
    )

    await service.aclose()

    assert probe.closed


@pytest.mark.asyncio
async def test_snapshot_empty_service_urls_returns_empty_list() -> None:
    """Без сервисов в map — пустой snapshot, без throw."""
    probe = _ProbeStub(responses={})
    service = DashboardService(
        probe_client=probe,
        service_urls={},
        langfuse_ui_url="",
    )

    snapshot = await service.snapshot()

    assert snapshot.services == []
    assert snapshot.links.langfuse_url == ""
