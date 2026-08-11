<script lang="ts">
	import { goto } from '$app/navigation';
	import { onMount } from 'svelte';
	import { authLogout } from '$lib/api';
	import { user } from '$lib/stores/user';

	let error: string | null = $state(null);

	onMount(async () => {
		try {
			await authLogout();
		} catch (e) {
			error = 'Не удалось выйти, попробуй ещё раз';
			console.error(e);
			return;
		}
		user.set(null);
		await goto('/login', { replaceState: true });
	});
</script>

<div class="max-w-sm mx-auto mt-24 text-center text-gray-400">
	{#if error}
		<p class="text-red-400">{error}</p>
		<a href="/login" class="text-indigo-400 transition-colors hover:text-indigo-300"
			>Перейти к входу</a
		>
	{:else}
		<p>Выходим…</p>
	{/if}
</div>
