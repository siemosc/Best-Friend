"""DashboardService: snapshot health сервисов + ссылки для admin UI.

Scope: core self-probe.
"""

import asyncio
from datetime import UTC, datetime
from typing import Protocol

from bestfiend.control_plane.dashboard.models import (
    DashboardHealthSnapshot,
    DashboardLinks,
    ServiceHealth,
)


class HealthProbe(Protocol):
    """Контракт health-probe клиента."""

    async def probe(self, name: str, url: str) -> ServiceHealth:
        """Пробует `{url}/health`, возвращает ServiceHealth (без throw)."""
        ...

    async def aclose(self) -> None:
        """Закрывает переиспользуемые ресурсы клиента (HTTP-коннекты)."""
        ...


class DashboardService:
    """Собирает snapshot health управляемых сервисов + ссылки."""

    __slots__ = ("_probe", "_service_urls", "_langfuse_ui_url")

    def __init__(
        self,
        *,
        probe_client: HealthProbe,
        service_urls: dict[str, str],
        langfuse_ui_url: str,
    ) -> None:
        self._probe = probe_client
        self._service_urls = dict(service_urls)
        self._langfuse_ui_url = langfuse_ui_url

    async def aclose(self) -> None:
        """Закрывает probe-клиент (владелец ресурса — сервис)."""
        await self._probe.aclose()

    async def snapshot(self) -> DashboardHealthSnapshot:
        """Parallel probe всех сервисов + упаковка в snapshot."""
        probes = [
            self._probe.probe(name, url) for name, url in self._service_urls.items()
        ]
        results = await asyncio.gather(*probes)
        return DashboardHealthSnapshot(
            services=list(results),
            links=DashboardLinks(langfuse_url=self._langfuse_ui_url),
            fetched_at=datetime.now(UTC),
        )
