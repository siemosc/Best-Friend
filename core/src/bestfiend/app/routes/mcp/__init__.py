"""HTTP-поверхность MCP-management: подключения, подписки, preview, OAuth-тракт."""

from bestfiend.app.routes.mcp.oauth_router import (
    create_mcp_oauth_router,
    register_mcp_oauth_exception_handlers,
)
from bestfiend.app.routes.mcp.router import (
    create_mcp_router,
    register_mcp_exception_handlers,
)


__all__ = [
    "create_mcp_oauth_router",
    "create_mcp_router",
    "register_mcp_exception_handlers",
    "register_mcp_oauth_exception_handlers",
]
