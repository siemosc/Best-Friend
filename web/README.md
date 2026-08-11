# web_ui

Frontend для BestFiend admin UI. SvelteKit + TypeScript + Tailwind, SPA-режим
(adapter-static). Ходит только в `control_plane` API.

## Требования

- Node.js ≥20
- pnpm (`npm install -g pnpm`)
- Запущенный `control_plane` на `http://localhost:8007`

## Запуск в dev

```bash
pnpm install
pnpm dev
```

Откроется `http://localhost:5173`. Vite проксирует `/api/*` в
`http://localhost:8007`, чтобы cookie `bestfiend_session` работали same-origin.

## Сборка прод-статики

```bash
pnpm build
```

Статика попадает в `build/`. Деплоить поверх nginx / CDN / любого static hosting.

## Проверка типов

```bash
pnpm check
```

## Страницы (Plan 4.2)

- `/login` — вход по логину и паролю.
- `/bind` — первый вход по 6-значному коду из Telegram-команды `/web`.
- `/dashboard` — заглушка с профилем текущего юзера.
- `/logout` — clears session и редиректит на `/login`.
