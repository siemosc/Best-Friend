"""HTTP-клиент health-probe для dashboard.

httpx.AsyncClient переиспользуется между пробами для поддержки keep-alive.
Никаких throw'ов: все ошибки маппятся в ServiceHealth(status=...).
"""

from datetime import UTC, datetime
import time

import httpx

from bestfiend.control_plane.dashboard.models import ServiceHealth


_HTTP_OK_MIN = 200
_HTTP_OK_MAX = 300
_ERROR_TRIM_CHARS = 200


class HealthProbeClient:
    """Переиспользуемый httpx-клиент для health-probe нескольких сервисов."""

    __slots__ = ("_client", "_timeout_s")

    def __init__(self, *, timeout_s: float) -> None:
        self._timeout_s = timeout_s
        self._client: httpx.AsyncClient | None = None

    async def aclose(self) -> None:
        """Закрывает переиспользуемый HTTP-клиент."""
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def probe(self, name: str, url: str) -> ServiceHealth:
        """Пробует `{url}/health`. Не бросает — все ошибки в ServiceHealth.status."""
        start = time.perf_counter()
        checked_at = datetime.now(UTC)
        target = f"{url.rstrip('/')}/health"
        try:
            response = await self._get_client().get(
                target,
                timeout=self._timeout_s,
            )
        except httpx.TimeoutException:
            return ServiceHealth(
                name=name,
                url=url,
                status="timeout",
                latency_ms=None,
                error="timed out",
                checked_at=checked_at,
            )
        except httpx.HTTPError as exc:
            return ServiceHealth(
                name=name,
                url=url,
                status="unreachable",
                latency_ms=None,
                error=str(exc)[:_ERROR_TRIM_CHARS],
                checked_at=checked_at,
            )

        latency_ms = int((time.perf_counter() - start) * 1000)
        if _HTTP_OK_MIN <= response.status_code < _HTTP_OK_MAX:
            return ServiceHealth(
                name=name,
                url=url,
                status="healthy",
                latency_ms=latency_ms,
                error=None,
                checked_at=checked_at,
            )
        return ServiceHealth(
            name=name,
            url=url,
            status="unhealthy",
            latency_ms=latency_ms,
            error=f"HTTP {response.status_code}",
            checked_at=checked_at,
        )

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=self._timeout_s)
        return self._client
