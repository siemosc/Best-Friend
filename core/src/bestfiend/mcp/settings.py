"""Настройки MCP discovery."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class McpDiscoverySettings(BaseSettings):
    """Таймаут опроса MCP-серверов в init-ноде.

    Применяется per-server при discovery (`initialize` + `list_tools`). Это НЕ
    таймаут исполнения `call_tool` — тот берётся из `ResolvedMcpServer.timeout_s`.
    """

    mcp_discovery_timeout_s: float = 10.0

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )
