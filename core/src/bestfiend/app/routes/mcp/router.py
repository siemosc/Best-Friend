"""HTTP-маршруты MCP-подключений, подписок и preview.

`create_mcp_router` — 8 эндпоинтов; `register_mcp_exception_handlers` маппит
`McpStorageError` (+ все подклассы) в error-контракт. Guards `require_admin`/
`require_session` из dependencies; ForbiddenError/InvalidSessionError ловит уже
существующий AuthError-handler приложения — не дублируем.
"""

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.responses import JSONResponse, Response

from bestfiend.app.routes.dependencies import (
    get_runtime,
    require_admin,
    require_session,
)
from bestfiend.app.routes.error_handlers import ErrorResponse
from bestfiend.app.routes.mcp.contracts import (
    CreateMcpConnectionRequest,
    DiscoveredToolView,
    DiscoverPreviewFailureView,
    DiscoverPreviewRequest,
    DiscoverPreviewResponse,
    McpConnectionView,
    McpServerSubscriptionView,
    SubscriptionView,
    UpdateMcpConnectionRequest,
    UpsertSubscriptionRequest,
)
from bestfiend.control_plane.auth.errors import ForbiddenError
from bestfiend.control_plane.mcp.errors import (
    McpStorageError,
    McpStorageUnavailableError,
    McpSubscriptionNotFoundError,
)
from bestfiend.control_plane.mcp.models import (
    McpConnectionWithOAuthClient,
    McpServerWithSubscription,
)
from bestfiend.control_plane.users.models import UserProfile
from bestfiend.mcp.contracts import ServerDiscovery


def create_mcp_router() -> APIRouter:
    """Роутер MCP-management: connections CRUD (admin) + subscriptions + preview (session)."""
    router = APIRouter()

    # ----- connections (admin) -----

    @router.get("/mcp/connections", response_model=list[McpConnectionView])
    async def list_connections(
        request: Request,
        _admin: UserProfile = Depends(require_admin),
    ) -> JSONResponse:
        svc = get_runtime(request).mcp_management_service
        records = await svc.list_connections()
        return JSONResponse(content=[_connection_payload(r) for r in records])

    @router.post("/mcp/connections", response_model=McpConnectionView)
    async def create_connection(
        payload: CreateMcpConnectionRequest,
        request: Request,
        _admin: UserProfile = Depends(require_admin),
    ) -> JSONResponse:
        svc = get_runtime(request).mcp_management_service
        item = await svc.create_connection(
            name=payload.name,
            url=payload.url,
            transport=payload.transport,
            auth_type=payload.auth_type,
            is_public=payload.is_public,
            timeout_s=payload.timeout_s,
            supports_parallel_tool_calls=payload.supports_parallel_tool_calls,
            oauth_client_id=payload.oauth_client_id,
            oauth_client_secret=payload.oauth_client_secret,
        )
        return JSONResponse(content=_connection_payload(item))

    @router.patch("/mcp/connections/{connection_id}", response_model=McpConnectionView)
    async def update_connection(
        connection_id: UUID,
        payload: UpdateMcpConnectionRequest,
        request: Request,
        _admin: UserProfile = Depends(require_admin),
    ) -> JSONResponse:
        svc = get_runtime(request).mcp_management_service
        # exclude_none: явный null для NOT NULL-поля = мусор, трактуем как «не трогать».
        # OAuth-креды идут отдельным аргументом, из fields их исключаем.
        fields = payload.model_dump(
            exclude_unset=True,
            exclude_none=True,
            exclude={"oauth_client_id", "oauth_client_secret"},
        )
        item = await svc.update_connection(
            connection_id,
            fields,
            oauth_client_id=payload.oauth_client_id,
            oauth_client_secret=payload.oauth_client_secret,
        )
        return JSONResponse(content=_connection_payload(item))

    @router.delete("/mcp/connections/{connection_id}", status_code=204)
    async def delete_connection(
        connection_id: UUID,
        request: Request,
        _admin: UserProfile = Depends(require_admin),
    ) -> Response:
        svc = get_runtime(request).mcp_management_service
        await svc.delete_connection(connection_id)
        return Response(status_code=204)

    # ----- subscriptions (user) -----

    @router.get("/mcp/my-servers", response_model=list[McpServerSubscriptionView])
    async def list_my_servers(
        request: Request,
        current: UserProfile = Depends(require_session),
    ) -> JSONResponse:
        svc = get_runtime(request).mcp_management_service
        rows = await svc.list_my_servers(current.user_id)
        return JSONResponse(content=[_my_server_payload(r) for r in rows])

    @router.put(
        "/mcp/subscriptions/{connection_id}",
        response_model=McpServerSubscriptionView,
    )
    async def upsert_subscription(
        connection_id: UUID,
        payload: UpsertSubscriptionRequest,
        request: Request,
        current: UserProfile = Depends(require_session),
    ) -> JSONResponse:
        svc = get_runtime(request).mcp_management_service
        await svc.upsert_subscription(
            current.user_id,
            connection_id,
            enabled=payload.enabled,
            auth_token=payload.auth_token,
            disabled_tools=payload.disabled_tools,
            timeout_s=payload.timeout_s,
        )
        # Re-read: отдаём богатый вью (connection + эффективная подписка) одним ответом.
        rows = await svc.list_my_servers(current.user_id)
        match = next((r for r in rows if r.connection_id == connection_id), None)
        if match is None:
            raise McpSubscriptionNotFoundError(
                f"subscription connection_id={connection_id} not found after upsert"
            )
        return JSONResponse(content=_my_server_payload(match))

    @router.delete("/mcp/subscriptions/{connection_id}", status_code=204)
    async def delete_subscription(
        connection_id: UUID,
        request: Request,
        current: UserProfile = Depends(require_session),
    ) -> Response:
        svc = get_runtime(request).mcp_management_service
        await svc.delete_subscription(current.user_id, connection_id)
        return Response(status_code=204)

    # ----- discover-preview -----

    @router.post("/mcp/discover-preview", response_model=DiscoverPreviewResponse)
    async def discover_preview(
        payload: DiscoverPreviewRequest,
        request: Request,
        current: UserProfile = Depends(require_session),
    ) -> JSONResponse:
        svc = get_runtime(request).mcp_management_service
        is_admin = current.role == "admin"
        # SSRF-guard: ad-hoc url (без connection_id) — только admin.
        if not is_admin and payload.connection_id is None:
            raise ForbiddenError("ad-hoc url preview is admin-only")
        discovery = await svc.discover_preview(
            is_admin=is_admin,
            user_id=current.user_id,
            connection_id=payload.connection_id,
            url=payload.url,
            auth_type=payload.auth_type,
            auth_token=payload.auth_token,
        )
        return JSONResponse(content=_preview_payload(discovery, payload.connection_id))

    return router


def register_mcp_exception_handlers(app: FastAPI) -> None:
    """Маппит McpStorageError (+ все подклассы) в стабильный error-контракт."""

    @app.exception_handler(McpStorageError)
    async def _handle_mcp_storage_error(
        _request: Request,
        exc: McpStorageError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=ErrorResponse(
                error_code=exc.error_code,
                detail=str(exc),
            ).model_dump(mode="json"),
        )


def _connection_payload(item: McpConnectionWithOAuthClient) -> dict[str, Any]:
    record = item.connection
    client = item.oauth_client
    return McpConnectionView(
        connection_id=record.connection_id,
        name=record.name,
        url=record.url,
        transport=record.transport,
        auth_type=record.auth_type,
        is_public=record.is_public,
        is_system=record.is_system,
        timeout_s=record.timeout_s,
        supports_parallel_tool_calls=record.supports_parallel_tool_calls,
        created_at=record.created_at,
        updated_at=record.updated_at,
        # client_secret наружу не отдаётся — светим только id и источник.
        oauth_client_id=client.client_id if client is not None else None,
        oauth_client_source=client.source if client is not None else None,
    ).model_dump(mode="json")


def _my_server_payload(record: McpServerWithSubscription) -> dict[str, Any]:
    subscription: SubscriptionView | None = None
    if record.has_subscription:
        # has_subscription True ⇒ sub_enabled/sub_created_at заполнены (см. _row_to_visible).
        if record.sub_enabled is None or record.sub_created_at is None:
            raise McpStorageUnavailableError(
                "MCP subscription record is missing required fields"
            )
        subscription = SubscriptionView(
            enabled=record.sub_enabled,
            auth_token=record.sub_auth_token,
            disabled_tools=record.sub_disabled_tools or [],
            timeout_s=record.sub_timeout_s,
            created_at=record.sub_created_at,
        )
    return McpServerSubscriptionView(
        connection_id=record.connection_id,
        name=record.name,
        url=record.url,
        transport=record.transport,
        auth_type=record.auth_type,
        is_public=record.is_public,
        is_system=record.is_system,
        timeout_s=record.timeout_s,
        subscription=subscription,
        oauth_status=record.oauth_status,
    ).model_dump(mode="json")


def _preview_payload(
    discovery: ServerDiscovery, requested_connection_id: UUID | None
) -> dict[str, Any]:
    failure = (
        DiscoverPreviewFailureView(
            kind=discovery.failure.kind, message=discovery.failure.message
        )
        if discovery.failure is not None
        else None
    )
    return DiscoverPreviewResponse(
        connection_id=requested_connection_id,  # ad-hoc → None (фиктивный id не светим)
        name=discovery.name,
        instructions=discovery.instructions,
        tools=[
            DiscoveredToolView(name=t.name, description=t.description)
            for t in discovery.tools
        ],
        failure=failure,
    ).model_dump(mode="json")
