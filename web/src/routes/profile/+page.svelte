<script lang="ts">
	import { ApiError, changePassword, updateProfile } from '$lib/api';
	import { user } from '$lib/stores/user';

	let timezoneDraft = $state($user?.timezone ?? '');
	let tzError: string | null = $state(null);
	let tzSuccess: boolean = $state(false);
	let tzLoading = $state(false);

	let cityDraft = $state($user?.city ?? '');
	let countryDraft = $state($user?.country ?? '');
	let locError: string | null = $state(null);
	let locSuccess: boolean = $state(false);
	let locLoading = $state(false);

	let currentPassword = $state('');
	let newPassword = $state('');
	let newPasswordConfirm = $state('');
	let pwError: string | null = $state(null);
	let pwSuccess: boolean = $state(false);
	let pwLoading = $state(false);

	let displayName = $derived($user?.login ?? $user?.user_id.slice(0, 8) ?? '—');
	let initial = $derived(displayName.charAt(0).toUpperCase());
	let tzDirty = $derived(timezoneDraft.trim() !== ($user?.timezone ?? ''));
	let locDirty = $derived(
		cityDraft.trim() !== ($user?.city ?? '') ||
			countryDraft.trim() !== ($user?.country ?? ''),
	);

	async function submitTimezone(event: SubmitEvent) {
		event.preventDefault();
		tzError = null;
		tzSuccess = false;
		tzLoading = true;
		try {
			const updated = await updateProfile({ timezone: timezoneDraft.trim() });
			user.set(updated);
			tzSuccess = true;
		} catch (e) {
			tzError = e instanceof ApiError ? e.message : 'Не удалось сохранить';
		} finally {
			tzLoading = false;
		}
	}

	async function submitLocation(event: SubmitEvent) {
		event.preventDefault();
		locError = null;
		locSuccess = false;
		locLoading = true;
		try {
			const updated = await updateProfile({
				city: cityDraft.trim() || null,
				country: countryDraft.trim() || null,
			});
			user.set(updated);
			locSuccess = true;
		} catch (e) {
			locError = e instanceof ApiError ? e.message : 'Не удалось сохранить';
		} finally {
			locLoading = false;
		}
	}

	async function submitPassword(event: SubmitEvent) {
		event.preventDefault();
		pwError = null;
		pwSuccess = false;
		if (newPassword !== newPasswordConfirm) {
			pwError = 'Новый пароль и подтверждение не совпадают';
			return;
		}
		if (newPassword.length < 8) {
			pwError = 'Новый пароль должен быть не короче 8 символов';
			return;
		}
		pwLoading = true;
		try {
			await changePassword({
				current_password: currentPassword,
				new_password: newPassword,
			});
			pwSuccess = true;
			currentPassword = '';
			newPassword = '';
			newPasswordConfirm = '';
		} catch (e) {
			pwError = e instanceof ApiError ? e.message : 'Не удалось сменить пароль';
		} finally {
			pwLoading = false;
		}
	}

	function statusBadge(status: string | undefined): string {
		if (status === 'active') return 'border-green-700/40 bg-green-500/15 text-green-300';
		if (status === 'pending') return 'border-amber-700/40 bg-amber-500/15 text-amber-200';
		return 'border-red-700/40 bg-red-500/15 text-red-300';
	}
</script>

<div class="page max-w-5xl space-y-6">
	<!-- Hero -->
	<section
		class="surface relative overflow-hidden bg-gradient-to-br from-gray-900 via-gray-900 to-indigo-950/40 p-6"
	>
		<div class="flex items-center gap-4">
			<div
				class="flex h-16 w-16 shrink-0 items-center justify-center rounded-full bg-gradient-to-br from-indigo-500 to-violet-600 text-2xl font-bold text-white shadow-glow"
			>
				{initial}
			</div>
			<div class="min-w-0 flex-1">
				<div class="flex flex-wrap items-center gap-2">
					<h1 class="truncate text-2xl font-semibold tracking-tight">{displayName}</h1>
					{#if $user?.role === 'admin'}
						<span class="badge border-indigo-500/30 bg-indigo-500/20 text-indigo-300">admin</span>
					{:else}
						<span class="badge border-gray-600/40 bg-gray-700/40 text-gray-300">user</span>
					{/if}
					{#if $user?.status}
						<span class="badge {statusBadge($user.status)}">{$user.status}</span>
					{/if}
				</div>
				<div class="mt-1 truncate font-mono text-sm text-gray-400">
					{$user?.user_id ?? '—'}
				</div>
			</div>
		</div>
	</section>

	<div class="grid grid-cols-1 gap-4 lg:grid-cols-2">
		<!-- Часовой пояс -->
		<section class="card space-y-4">
			<header class="section-head">
				<span class="section-icon border-blue-700/30 bg-blue-500/15 text-blue-300" aria-hidden="true"
					>🕐</span
				>
				<div>
					<h2 class="section-title">Часовой пояс</h2>
					<p class="section-sub">IANA-имя, напр. Europe/Belgrade, Europe/Moscow.</p>
				</div>
			</header>
			<form onsubmit={submitTimezone} class="space-y-3">
				<label class="block">
					<span class="field-label">Timezone</span>
					<input
						bind:value={timezoneDraft}
						class="input font-mono"
						placeholder="Europe/Belgrade"
						required
						maxlength="64"
					/>
				</label>
				{#if tzError}<p class="text-sm text-red-400">{tzError}</p>{/if}
				{#if tzSuccess && !tzDirty}<p class="text-sm text-green-400">✓ Сохранено</p>{/if}
				<div class="flex items-center justify-between gap-2">
					{#if tzDirty}
						<span class="inline-flex items-center gap-1.5 text-xs text-amber-400">
							<span class="h-1.5 w-1.5 rounded-full bg-amber-400"></span> не сохранено
						</span>
					{:else}
						<span class="text-xs text-gray-500">сохранено</span>
					{/if}
					<button type="submit" disabled={tzLoading || !tzDirty} class="btn-primary btn-sm">
						{tzLoading ? 'Сохраняем…' : 'Сохранить'}
					</button>
				</div>
			</form>
		</section>

		<!-- Локация -->
		<section class="card space-y-4">
			<header class="section-head">
				<span
					class="section-icon border-purple-700/30 bg-purple-500/15 text-purple-300"
					aria-hidden="true">📍</span
				>
				<div>
					<h2 class="section-title">Локация</h2>
					<p class="section-sub">Город и страна — для LLM-контекста и временных подсказок.</p>
				</div>
			</header>
			<form onsubmit={submitLocation} class="space-y-3">
				<label class="block">
					<span class="field-label">Город</span>
					<input bind:value={cityDraft} class="input" placeholder="Belgrade" maxlength="128" />
				</label>
				<label class="block">
					<span class="field-label">Страна</span>
					<input bind:value={countryDraft} class="input" placeholder="Serbia" maxlength="128" />
				</label>
				{#if locError}<p class="text-sm text-red-400">{locError}</p>{/if}
				{#if locSuccess && !locDirty}<p class="text-sm text-green-400">✓ Сохранено</p>{/if}
				<div class="flex items-center justify-between gap-2">
					{#if locDirty}
						<span class="inline-flex items-center gap-1.5 text-xs text-amber-400">
							<span class="h-1.5 w-1.5 rounded-full bg-amber-400"></span> не сохранено
						</span>
					{:else}
						<span class="text-xs text-gray-500">сохранено</span>
					{/if}
					<button type="submit" disabled={locLoading || !locDirty} class="btn-primary btn-sm">
						{locLoading ? 'Сохраняем…' : 'Сохранить'}
					</button>
				</div>
			</form>
		</section>

		<!-- Привязанные каналы -->
		<section class="card space-y-4 lg:col-span-2">
			<header class="section-head">
				<span
					class="section-icon border-emerald-700/30 bg-emerald-500/15 text-emerald-300"
					aria-hidden="true">🔗</span
				>
				<div>
					<h2 class="section-title">Привязанные каналы</h2>
					<p class="section-sub">Привязка делается через Telegram-бота: /web и /discord.</p>
				</div>
			</header>
			<ul class="grid grid-cols-1 gap-2 sm:grid-cols-3">
				<li
					class="flex items-center justify-between gap-3 rounded-lg border border-gray-800 bg-gray-950/40 px-3 py-2.5"
				>
					<div class="flex min-w-0 items-center gap-3">
						<span
							class="flex h-8 w-8 items-center justify-center rounded-md bg-sky-500/15 text-sm text-sky-300"
							aria-hidden="true">TG</span
						>
						<div class="min-w-0">
							<div class="text-sm font-medium">Telegram</div>
							<div class="text-xs text-gray-500">chat_id</div>
						</div>
					</div>
					<div class="truncate font-mono text-sm">
						{#if $user?.telegram_chat_id !== null && $user?.telegram_chat_id !== undefined}
							<span class="text-green-300">{$user.telegram_chat_id}</span>
						{:else}
							<span class="text-gray-500">—</span>
						{/if}
					</div>
				</li>
				<li
					class="flex items-center justify-between gap-3 rounded-lg border border-gray-800 bg-gray-950/40 px-3 py-2.5"
				>
					<div class="flex min-w-0 items-center gap-3">
						<span
							class="flex h-8 w-8 items-center justify-center rounded-md bg-indigo-500/15 text-sm text-indigo-300"
							aria-hidden="true">DC</span
						>
						<div class="min-w-0">
							<div class="text-sm font-medium">Discord</div>
							<div class="text-xs text-gray-500">user_id</div>
						</div>
					</div>
					<div class="truncate font-mono text-sm">
						{#if $user?.discord_user_id}
							<span class="text-green-300">{$user.discord_user_id}</span>
						{:else}
							<span class="text-gray-500">—</span>
						{/if}
					</div>
				</li>
				<li
					class="flex items-center justify-between gap-3 rounded-lg border border-gray-800 bg-gray-950/40 px-3 py-2.5"
				>
					<div class="flex min-w-0 items-center gap-3">
						<span
							class="flex h-8 w-8 items-center justify-center rounded-md bg-gray-700/40 text-sm text-gray-300"
							aria-hidden="true">ID</span
						>
						<div class="min-w-0">
							<div class="text-sm font-medium">Login</div>
							<div class="text-xs text-gray-500">для входа в web</div>
						</div>
					</div>
					<div class="truncate font-mono text-sm text-gray-200">{$user?.login ?? '—'}</div>
				</li>
			</ul>
		</section>
	</div>

	<!-- Смена пароля -->
	<section class="card space-y-4">
		<header class="section-head">
			<span class="section-icon border-amber-700/30 bg-amber-500/15 text-amber-300" aria-hidden="true"
				>🔒</span
			>
			<div>
				<h2 class="section-title">Смена пароля</h2>
				<p class="section-sub">Минимум 8 символов. После смены сессия сохранится.</p>
			</div>
		</header>
		<form onsubmit={submitPassword} class="space-y-3">
			<div class="grid grid-cols-1 gap-3 md:grid-cols-3">
				<label class="block">
					<span class="field-label">Текущий пароль</span>
					<input
						bind:value={currentPassword}
						type="password"
						class="input"
						required
						autocomplete="current-password"
					/>
				</label>
				<label class="block">
					<span class="field-label">Новый пароль</span>
					<input
						bind:value={newPassword}
						type="password"
						class="input"
						required
						minlength="8"
						autocomplete="new-password"
					/>
				</label>
				<label class="block">
					<span class="field-label">Подтверждение</span>
					<input
						bind:value={newPasswordConfirm}
						type="password"
						class="input"
						required
						minlength="8"
						autocomplete="new-password"
					/>
				</label>
			</div>
			{#if pwError}<p class="text-sm text-red-400">{pwError}</p>{/if}
			{#if pwSuccess}<p class="text-sm text-green-400">✓ Пароль обновлён</p>{/if}
			<div class="flex justify-end">
				<button type="submit" disabled={pwLoading} class="btn-primary">
					{pwLoading ? 'Меняем…' : 'Сменить пароль'}
				</button>
			</div>
		</form>
	</section>
</div>
