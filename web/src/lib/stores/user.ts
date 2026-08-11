// Svelte store с текущим профилем. null = не залогинен.

import { writable } from 'svelte/store';
import type { UserResponse } from '$lib/types';

export const user = writable<UserResponse | null>(null);
