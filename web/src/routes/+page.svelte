<script lang="ts">
	import { goto } from '$app/navigation';
	import { onMount } from 'svelte';
	import { user } from '$lib/stores/user';
	import { get } from 'svelte/store';

	onMount(() => {
		const u = get(user);
		void goto(u ? '/dashboard' : '/login');
	});
</script>

<div class="mx-auto mt-24 flex max-w-xl flex-col items-center gap-3 text-gray-400">
	<span class="spinner h-6 w-6" aria-hidden="true"></span>
	<p class="text-sm">Перенаправление…</p>
</div>
