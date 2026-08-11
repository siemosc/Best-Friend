# Модуль control_plane/ — identity, конфиги, MCP-менеджмент, дашборд

`core/src/bestfiend/control_plane/`. Пользователи/сессии/роли, per-user конфиг ассистента, реестр LLM-моделей, admin-CRUD MCP, health-дашборд. Таблицы — `mem:db_schema`.

**HTTP модулю не принадлежит.** Ни одного `APIRouter`, ни одного импорта fastapi: роутеры, DTO, guards и session-cookie живут в `app/routes/` (`mem:modules/app`). Модуль отдаёт наружу сервисы и доменные ошибки, транспорт собирает их сам. Раскладка вертикальная — подпакет на область.

## users/

`models.py` (UserProfile, UserRole, UserStatus), `errors.py`, `repository.py` (UserRepository — таблица users), `service.py` (UserService). Конфликты уникальности: репозиторий транслирует UniqueViolation в UserConflictError, `update_profile`/`resolve_or_create` ловят только её, не голый Exception.

## auth/

`service.py` — AuthService (сессии, логин, bind); writer-зависимости (session/binding/auth_user-репозитории, user_service) обязательны в ctor, reader-режима нет. `repository.py` — SessionRepository, BindingCodeRepository, AuthUserRepository. `passwords.py` — hash_password/verify_password (bcrypt, cost дефолт 12). `models.py`, `errors.py`. `AuthSettings` — в корневом `settings.py`.

- Сессии — HttpOnly cookie `bestfiend_session` = UUID, TTL 30 дней, SameSite=lax, secure=False (dev). Чтение/установка cookie — `app/routes/cookies.py`.
- Привязка Telegram→web: `/web` в боте → 6-значный криптостойкий binding code (TTL 10 мин, retry по коллизии) → POST /auth/bind (code+login+password) создаёт креды и сессию.
- Гочи: осиротевшая сессия (юзер удалён) вычищается на read; смена пароля НЕ инвалидирует другие сессии (MVP); протухшие сессии проверяются на чтении, cleanup-job нет.

## model_registry/

Резолв конфига LLM для графа: `ModelRegistry.resolve(ResolveModelRequest)` → ResolveModelResponse (config + instruction + UserEnvironment из users.timezone/city/country). Единственный подпакет без HTTP-поверхности: потребитель — `graph/runtime.py`, не админка.
- Дефолт — models.config по model_id (env MODEL_ID, дефолт "orchestrator-default").
- **llm_custom_config юзера непустой → ПОЛНАЯ замена дефолта, не merge.**
- user_instruction пустая → None (в промпт не попадает).

## assistant/

user_assistant_configs: bootstrap_for_user (идемпотентный, side-effect создания юзера), get_for_user (lazy-bootstrap self-healing), update (exclude_unset), reset ("" / {}).

## mcp/ (менеджмент; сам клиент — `mem:modules/mcp`)

- Admin-CRUD mcp_connections; user-операции над mcp_subscriptions (upsert/delete), видимость = public ∪ свои подписки.
- Инварианты: public connection ⇒ auth_type ∈ {none, oauth}, bearer запрещён; is_system нельзя удалить; UNIQUE (user_id, connection_id); auth_type=none ⇒ auth_token гасится при резолве (`_row_to_resolved` и preview — легаси-токен подписки не уезжает в заголовок); OAuth-креды только при auth_type=oauth, секрет без client_id отклоняется, пустые строки нормализуются в None.
- Резолв для графа — `resolve.py`: McpResolveService (реализация McpServerResolver) = repo.list_for_user + живой access для oauth через fresh_access_token; oauth-сервер без живого токена исключается из выдачи, чтобы не бить 401 в discovery каждым запросом. Репозиторий резолвером больше не является (замена в app/runtime.py).
- discover-preview — test-connection: by-id для юзера (oauth → fresh-токен), ad-hoc URL только admin (SSRF-барьер); сбой → в `.failure`, не throw.
- Guard is_system живёт в McpManagementService; admin-выдача connections — композиция McpConnectionWithOAuthClient, client_secret наружу не сериализуется (закрыто тестом роутера).
- HTTP-контракты — `app/routes/mcp/contracts.py`; OAuth-эндпоинты — `app/routes/mcp/oauth_router.py` (start JSON / браузерный callback с redirect на /mcp / disconnect 204).

## mcp/oauth/ — OAuth2-подключение юзера к MCP-серверу (2026-07-22)

Split-flow: между «выдать authorization URL» и «обменять code» состояние живёт в Postgres (таблицы — `mem:db_schema`). Кубики протокола — mcp SDK (`mcp.client.auth.utils`, `mcp.shared.auth`), прямая зависимость `mcp>=1.27,<2`. fastmcp-хелпер `OAuth` не используется: он открывает браузер и слушает localhost, для web-backend непригоден.

- `discovery.py` — 401-проба → WWW-Authenticate → PRM (RFC 9728) → AS metadata (RFC 8414, OIDC-фолбэк); mix-up защита: PRM.resource сверяется с server_url (check_resource_allowed). AuthorizationServerInfo несёт issuer и scope_hint (приоритет scope: WWW-Authenticate → PRM → AS metadata).
- `token_client.py` — обмен code, refresh, DCR; клиентская аутентификация своя (client_secret_basic/post/none — публичного API в SDK нет); invalid_grant на refresh → McpOAuthRefreshRejectedError (внутренняя, наружу не мапится).
- `service.py` — McpOAuthService. start_flow: DCR при registration_endpoint, иначе преднастроенные креды (Google DCR/CIMD не поддерживает — только client_id/secret из Cloud Console); PKCE S256 обязателен; authorization URL несёт resource (RFC 8707) + access_type=offline&prompt=consent (иначе Google не отдаёт refresh_token). complete_flow: state одноразовый (DELETE..RETURNING), сверка user и iss (RFC 9207). fresh_access_token: скью 60 с; refresh под per-(user,connection) asyncio.Lock + CAS по refresh_token против гонки ротации; ответ без refresh_token сохраняет старый. status_for: not_connected|connected|expired. Фасады для management: upsert_preregistered_client, get_client(s).
- Ошибки с error_code: mcp_oauth_client_missing 409, discovery/exchange/registration_failed 502, flow_expired 410.
- redirect_uri = `{public_base_url}/api/mcp/oauth/callback`; PublicUrlSettings (env PUBLIC_BASE_URL, дефолт http://localhost:5173) — app/settings.py, сборка стека в app/runtime.py.
- Google-грабли вне кода: пока OAuth-приложение в Cloud Console в статусе Testing, refresh-токены живут 7 дней; лечится переводом в In production (verification не обязательна — предупреждение «unverified» кликается).

## dashboard/

`client.py` — health-пробы; `service.py` — параллельный опрос + ссылки (langfuse_url). Статусы healthy/unhealthy/timeout/unreachable.

## db.py

Protocol `ControlPlaneDatabaseClient` (execute/fetch/fetch_one) — порт БД. Реализацией владеет `app/db.py`, модуль не импортирует app.

## Конвенции ошибок

Доменные ошибки несут error_code (фронт матчится по коду, не по тексту). Статусы жёсткие: 401/403/404/409, 410 = протухший binding code, 503 = БД недоступна. Валидация входа и `extra="forbid"` — на request-моделях в `app/routes/`.
