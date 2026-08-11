import { redirect } from '@sveltejs/kit';
import { get } from 'svelte/store';
import { authMe, ApiError } from '$lib/api';
import { user } from '$lib/stores/user';
import type { LayoutLoad } from './$types';

// SPA-режим: отключаем SSR и prerender, рендер только в браузере.
export const ssr = false;
export const prerender = false;

const PUBLIC_ROUTES = new Set(['/login', '/bind']);

export const load: LayoutLoad = async ({ url }) => {
	let current = get(user);

	if (current === null) {
		try {
			current = await authMe();
			user.set(current);
		} catch (e) {
			if (!(e instanceof ApiError) || e.status !== 401) {
				// Непредвиденная ошибка — показываем /login, но логируем
				console.error('auth/me failed:', e);
			}
		}
	}

	const isPublic = PUBLIC_ROUTES.has(url.pathname);

	if (current === null && !isPublic) {
		throw redirect(302, '/login');
	}
	if (current !== null && isPublic) {
		throw redirect(302, '/dashboard');
	}

	return { user: current };
};
