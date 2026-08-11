"""Dashboard capability: snapshot health сервисов для admin UI.

Scope: core self-probe. Внешние probes (embedding и т.п.)
не реализованы — добавляются по запросу.
"""

from bestfiend.control_plane.dashboard.client import HealthProbeClient
from bestfiend.control_plane.dashboard.models import (
    DashboardHealthSnapshot,
    DashboardLinks,
    ServiceHealth,
    ServiceHealthStatus,
)
from bestfiend.control_plane.dashboard.service import DashboardService
from bestfiend.control_plane.dashboard.settings import DashboardSettings


__all__ = [
    "DashboardHealthSnapshot",
    "DashboardLinks",
    "DashboardService",
    "DashboardSettings",
    "HealthProbeClient",
    "ServiceHealth",
    "ServiceHealthStatus",
]
