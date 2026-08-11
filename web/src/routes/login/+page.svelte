<script lang="ts">
	import { goto } from '$app/navigation';
	import { ApiError, authLogin } from '$lib/api';
	import { user } from '$lib/stores/user';

	let login = $state('');
	let password = $state('');
	let error: string | null = $state(null);
	let loading = $state(false);

	async function submit(event: SubmitEvent) {
		event.preventDefault();
		error = null;
		loading = true;
		try {
			const profile = await authLogin(login, password);
			user.set(profile);
			await goto('/dashboard');
		} catch (e) {
			error =
				e instanceof ApiError ? e.message : 'Не удалось войти, попробуй позже';
		} finally {
			loading = false;
		}
	}
</script>

<div class="mx-auto mt-20 max-w-sm space-y-5 px-4">
	<div class="flex flex-col items-center gap-3">
		<span
			class="inline-flex h-14 w-14 items-center justify-center rounded-2xl bg-gradient-to-br from-indigo-500 to-violet-600 text-2xl font-bold text-white shadow-glow"
			>B</span
		>
		<div class="text-center">
			<h1 class="text-xl font-semibold tracking-tight">BestFiend</h1>
			<p class="text-sm text-gray-400">Вход в admin UI</p>
		</div>
	</div>

	<section class="surface animate-fade-in-up space-y-4 p-6">
		<form onsubmit={submit} class="space-y-4">
			<label class="block">
				<span class="field-label">Логин</span>
				<input
					bind:value={login}
					class="input"
					placeholder="login"
					required
					minlength="3"
					maxlength="64"
					autocomplete="username"
				/>
			</label>
			<label class="block">
				<span class="field-label">Пароль</span>
				<input
					bind:value={password}
					type="password"
					class="input"
					required
					minlength="8"
					autocomplete="current-password"
				/>
			</label>

			{#if error}
				<p class="text-sm text-red-400">{error}</p>
			{/if}

			<button type="submit" disabled={loading} class="btn-primary w-full">
				{loading ? 'Входим…' : 'Войти'}
			</button>
		</form>

		<div class="border-t border-gray-800 pt-4 text-center text-sm text-gray-400">
			Первый вход?
			<a href="/bind" class="text-indigo-400 transition-colors hover:text-indigo-300"
				>Ввести код из Telegram</a
			>
		</div>
	</section>
</div>
