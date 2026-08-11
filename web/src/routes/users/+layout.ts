import { redirect } from '@sveltejs/kit';
import { get } from 'svelte/store';
import { user } from '$lib/stores/user';
import type { LayoutLoad } from './$types';

// Admin-guard: страница /users доступна только role=admin.
// Родительский +layout.ts уже гарантирует, что user в store (иначе был бы 401 redirect).
export const load: LayoutLoad = async () => {
	const current = get(user);
	if (current === null || current.role !== 'admin') {
		throw redirect(302, '/dashboard');
	}
	return { admin: current };
};
