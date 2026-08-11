# web/ — SvelteKit SPA (админка + управление памятью)

SvelteKit 2 + Svelte 5, Tailwind, pnpm, adapter-static, **SPA-режим: ssr=false, prerender=false**. Dev: vite на 5173, `/api` проксируется на core :8010. Auth — cookie `bestfiend_session` (`credentials: 'include'`), layout-load зовёт GET /auth/me → redirect на /login при 401; admin-ограничения UI дублируются backend-guard'ом self_or_admin.

## Маршруты (web/src/routes/)

- `/login`, `/bind` — вход и привязка по 6-значному коду из Telegram.
- `/dashboard` — health сервисов (polling 5s) + ссылка Langfuse.
- `/assistant` — user_instruction + llm_custom_config (admin выбирает юзера).
- `/memory` — вкладка памяти: overview, notes (фильтры kind/subject/status/entity/q), create/edit/delete/revise, entities, ops-лог, turns.
- `/mcp` — my-servers, подписки, discover-preview; OAuth-подключение сервера (статус-бейдж connected/expired/not_connected, кнопки Подключить/Переподключить → `startMcpOauth` + переход по authorization_url, Отключить; для oauth-серверов поле auth_token скрыто). Admin-форма: auth_type oauth + client_id/client_secret (write-only, пусто = DCR). Возврат с callback — баннеры `?oauth_connected`/`?oauth_error`, query чистится replaceState. Callback ходит через vite-proxy: `PUBLIC_BASE_URL` (core) в dev = `http://localhost:5173`.
- `/profile`, `/users` (admin), `/logout`.

## lib

- `lib/api.ts` — единственный API-клиент: все вызовы core (auth, users, assistant, dashboard, mcp, memory). ApiError(status, errorCode) — матч по errorCode; formatErrorDetail нормализует FastAPI 422. buildQuery — массивы как повторяемые параметры.
- `lib/types.ts` — **ручное зеркало** control_plane/memory contracts (не codegen); при изменении pydantic-контрактов обновлять руками.
- `lib/stores/user.ts` — writable<UserResponse|null>.
- `lib/components/` — DecimalInput, HelpHint, memory/*.

## Дизайн-система

Единая (введена v29.5.1): тёмная тема (фон #0a0e17 navy-slate, акцент indigo-500), утилитарные классы в глобальном CSS: `.page`, `.page-title`, `.surface` (border + bg-gray-900/50 + rounded-xl), `.card` (surface+p-5), `.section-head`. Новые страницы строить из этих классов, не изобретать локальные стили.

Проверка: `pnpm check` (svelte-check). Чат-интерфейса в web нет — диалог идёт через Telegram; SSE-поверхности у core не осталось (снесена v29.9.0).