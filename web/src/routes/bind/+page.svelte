<script lang="ts">
	import { goto } from '$app/navigation';
	import { ApiError, authBind } from '$lib/api';
	import { user } from '$lib/stores/user';

	let code = $state('');
	let login = $state('');
	let password = $state('');
	let error: string | null = $state(null);
	let loading = $state(false);

	async function submit(event: SubmitEvent) {
		event.preventDefault();
		error = null;
		loading = true;
		try {
			const profile = await authBind(code, login, password);
			user.set(profile);
			await goto('/dashboard');
		} catch (e) {
			error =
				e instanceof ApiError
					? e.message
					: 'Не удалось привязать аккаунт, попробуй позже';
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
			<h1 class="text-xl font-semibold tracking-tight">Первый вход</h1>
			<p class="text-sm text-gray-400">Привязка web-аккаунта к Telegram.</p>
		</div>
	</div>

	<section class="surface animate-fade-in-up space-y-4 p-6">
		<p class="rounded-lg border border-gray-800 bg-gray-950/40 p-3 text-xs leading-relaxed text-gray-400">
			Отправь боту команду
			<code class="rounded bg-gray-800 px-1.5 py-0.5 text-gray-200">/web</code>, он
			пришлёт 6-значный код. Введи код ниже и придумай логин и пароль.
		</p>

		<form onsubmit={submit} class="space-y-4">
			<label class="block">
				<span class="field-label">Код из Telegram</span>
				<input
					bind:value={code}
					class="input text-center text-lg font-mono tracking-[0.5em]"
					placeholder="123456"
					required
					minlength="6"
					maxlength="6"
					pattern="\d{'{'}6{'}'}"
					inputmode="numeric"
					autocomplete="one-time-code"
				/>
			</label>
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
				<span class="field-label">Пароль (мин. 8 символов)</span>
				<input
					bind:value={password}
					type="password"
					class="input"
					required
					minlength="8"
					autocomplete="new-password"
				/>
			</label>

			{#if error}
				<p class="text-sm text-red-400">{error}</p>
			{/if}

			<button type="submit" disabled={loading} class="btn-primary w-full">
				{loading ? 'Привязываем…' : 'Привязать и войти'}
			</button>
		</form>

		<div class="border-t border-gray-800 pt-4 text-center text-sm text-gray-400">
			Уже есть логин?
			<a href="/login" class="text-indigo-400 transition-colors hover:text-indigo-300">Войти</a>
		</div>
	</section>
</div>
