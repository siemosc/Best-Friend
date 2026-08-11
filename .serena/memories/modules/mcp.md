# Модуль mcp/ — MCP-клиент (fastmcp)

`core/src/bestfiend/mcp/`. Подключение к внешним MCP-серверам, discovery тулов, нормализация результатов. Конфиг серверов живёт в БД control_plane (mcp_connections/mcp_subscriptions — `mem:modules/control_plane`), сюда приходит уже резолвнутый `ResolvedMcpServer`.

## client.py — McpClient

- Транспорт: StreamableHttpTransport(url) + bearer по наличию auth_token, тип не проверяется: резолв гарантирует none ⇒ None, oauth ⇒ живой access (`mem:modules/control_plane`).
- `discover()` → (instructions из init, list_tools → ToolInfo); любой сбой — исключение (ловит discovery).
- `call_tool(name, args, meta)` → CallToolResult: `raise_on_error=False` (tool-ошибка — валидный результат, не exception); timeout = server.timeout_s; meta → нативный `_meta` payload (внутри — user_id).
- Классификация ошибок (mcp/errors.py): база `McpClientError(Exception)`; наследники McpAuthError (401/403), McpConnectError (транспорт), McpProtocolError (остальное). Имя базы НЕ McpError — оно занято SDK (mcp.shared.exceptions.McpError, пробрасывается fastmcp). McpDiscoveryError удалён (v29.9.3).

## discovery.py

`discover_servers(servers, settings)` — параллельный gather без кэша (каждый запрос опрашивает вживую). Graceful degradation: сбой одного сервера → `ServerDiscovery.failure` (timeout/auth/unreachable/protocol), batch не падает, граф шлёт ProgressStep о недоступном сервере и работает с остальными.

## coercion.py — контрактный диспетчер результатов

`coerce_tool_result(CallToolResult)` → (content: str, artifacts: list[ArtifactRef]|None). Диспатч по СТРУКТУРЕ, не по имени сервера:
1. is_error → текст ошибки в content;
2. structured_content парсится в McpToolPayload `{result: str, artifacts: list[ArtifactRef]}` → при наличии artifacts content = result + MD-блок «Созданные артефакты» (artifact_llm_name — description), рефы в artifact-канал;
3. иначе generic text (text-блоки или JSON structured_content).

## Связка с графом (tool_builder в graph/)

Namespacing тулов: `{server.name}__{raw_tool_name}` — маппинг запечён в closure StructuredTool (response_format="content_and_artifact"). disabled_tools вычитаются по raw-имени ДО namespacing. serial_tool_servers наполняется только серверами с supports_parallel_tool_calls=false. request_meta (user_id) запекается в closure при построении.