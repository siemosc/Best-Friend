"""Резолв MCP-серверов для графа с подстановкой живых OAuth-токенов.

`McpResolveService` реализует контракт `McpServerResolver`: берёт список подписок
из репозитория и для oauth-серверов подставляет свежий access-токен. Заменяет
прямое использование репозитория как резолвера — refresh токенов виден графу.
"""

from uuid import UUID

from bestfiend.contracts.mcp import ResolvedMcpServer
from bestfiend.control_plane.mcp.oauth.service import McpOAuthService
from bestfiend.control_plane.mcp.repository import McpSubscriptionRepository


class McpResolveService:
    """Резолвер серверов юзера для графа: подписки + живой access для oauth."""

    __slots__ = ("_subscriptions", "_oauth")

    def __init__(
        self,
        *,
        subscription_repository: McpSubscriptionRepository,
        oauth_service: McpOAuthService,
    ) -> None:
        self._subscriptions = subscription_repository
        self._oauth = oauth_service

    async def list_for_user(self, user_id: UUID) -> list[ResolvedMcpServer]:
        """Возвращает серверы юзера; oauth без живого токена исключает из выдачи.

        Для auth_type == "oauth" кладёт свежий access в auth_token. Токена нет
        (юзер не подключал / протух без refresh / refresh отвергнут) — сервер
        выпадает из списка: public-oauth без подключения не должен каждым запросом
        ловить 401 в discovery графа. Статус «требует подключения» юзер видит на
        /mcp, не в ProgressStep.
        """
        servers = await self._subscriptions.list_for_user(user_id)
        resolved: list[ResolvedMcpServer] = []
        for server in servers:
            if server.auth_type != "oauth":
                resolved.append(server)
                continue
            access = await self._oauth.fresh_access_token(
                user_id, server.connection_id
            )
            if access is None:
                continue
            resolved.append(server.model_copy(update={"auth_token": access}))
        return resolved
