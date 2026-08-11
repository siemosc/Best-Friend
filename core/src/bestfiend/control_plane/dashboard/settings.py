"""Настройки dashboard health-probe и внешних ссылок.

Service URLs не зашиваются в settings — собираются в `build_runtime` из
core self URL и инжектятся в DashboardService.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class DashboardSettings(BaseSettings):
    """Настройки health-probe + внешние dashboard-ссылки."""

    dashboard_health_timeout_s: float = 2.0
    core_self_url: str = "http://127.0.0.1:8010"
    langfuse_ui_url: str = ""
    langfuse_project_id: str | None = None

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    def langfuse_link(self) -> str:
        """Полный URL для UI: project page (за 30д) если задан id, иначе base."""
        base = self.langfuse_ui_url.rstrip("/")
        if not base:
            return ""
        if self.langfuse_project_id:
            return f"{base}/project/{self.langfuse_project_id}?dateRange=30d"
        return base
