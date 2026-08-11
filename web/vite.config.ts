import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';

// web ходит через /api/*. Весь /api трафик идёт в core (:8010). Anchored
// keys ниже — для явности (специфика per-endpoint и порядок матчинга),
// default `/api` — catch-all для всего, что не покрыто.
export default defineConfig({
	plugins: [sveltekit()],
	server: {
		port: 5173,
		proxy: {
			// ── Админ/auth endpoints на :8010 ──────────────────────────
			// Порядок важен: специфические ключи (users/me, /assistant-config)
			// идут ДО общего '^/api/users/[^/]+$' чтобы не ловиться им.
			'^/api/users$': {
				target: 'http://localhost:8010',
				changeOrigin: true,
				rewrite: (path) => path.replace(/^\/api/, ''),
			},
			'^/api/users/me$': {
				target: 'http://localhost:8010',
				changeOrigin: true,
				rewrite: (path) => path.replace(/^\/api/, ''),
			},
			'^/api/users/[^/]+/assistant-config(/reset)?$': {
				target: 'http://localhost:8010',
				changeOrigin: true,
				rewrite: (path) => path.replace(/^\/api/, ''),
			},
			'^/api/users/[^/]+$': {
				target: 'http://localhost:8010',
				changeOrigin: true,
				rewrite: (path) => path.replace(/^\/api/, ''),
			},
			'^/api/auth/me$': {
				target: 'http://localhost:8010',
				changeOrigin: true,
				rewrite: (path) => path.replace(/^\/api/, ''),
			},
			'^/api/auth/(login|bind|logout|change-password)$': {
				target: 'http://localhost:8010',
				changeOrigin: true,
				rewrite: (path) => path.replace(/^\/api/, ''),
			},
			'^/api/dashboard/health$': {
				target: 'http://localhost:8010',
				changeOrigin: true,
				rewrite: (path) => path.replace(/^\/api/, ''),
			},
			// Default catch-all на core. Любой /api/* не покрытый
			// anchored ключами выше — попадает сюда (core ответит 404 если
			// path не зарегистрирован).
			'/api': {
				target: 'http://localhost:8010',
				changeOrigin: true,
				rewrite: (path) => path.replace(/^\/api/, ''),
			},
		},
	},
});
