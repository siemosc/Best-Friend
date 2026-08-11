"""Pydantic-модели snapshot для /dashboard/health."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


ServiceHealthStatus = Literal["healthy", "unhealthy", "timeout", "unreachable"]


class ServiceHealth(BaseModel):
    """Результат health-probe одного сервиса."""

    model_config = ConfigDict(extra="forbid")

    name: str
    url: str
    status: ServiceHealthStatus
    latency_ms: int | None
    error: str | None = None
    checked_at: datetime


class DashboardLinks(BaseModel):
    """Внешние ссылки для UI (Langfuse и будущие)."""

    model_config = ConfigDict(extra="forbid")

    langfuse_url: str


class DashboardHealthSnapshot(BaseModel):
    """Полный snapshot для /dashboard/health."""

    model_config = ConfigDict(extra="forbid")

    services: list[ServiceHealth]
    links: DashboardLinks
    fetched_at: datetime
