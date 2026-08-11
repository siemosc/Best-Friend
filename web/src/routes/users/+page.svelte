<script lang="ts">
	import { onMount } from 'svelte';
	import { adminUpdateUser, ApiError, listUsers } from '$lib/api';
	import { user as currentUser } from '$lib/stores/user';
	import type { UserResponse, UserRole, UserStatus } from '$lib/types';

	const ROLES: UserRole[] = ['user', 'admin'];
	const STATUSES: UserStatus[] = ['pending', 'active', 'banned'];

	let users: UserResponse[] = $state([]);
	let loading = $state(true);
	let error: string | null = $state(null);

	async function load() {
		error = null;
		try {
			users = await listUsers();
		} catch (e) {
			error = e instanceof ApiError ? e.message : 'Не удалось загрузить список';
		} finally {
			loading = false;
		}
	}

	function isSelf(u: UserResponse): boolean {
		return $currentUser !== null && u.user_id === $currentUser.user_id;
	}

	async function changeRole(u: UserResponse, event: Event) {
		const target = event.currentTarget as HTMLSelectElement;
		const newRole = target.value as UserRole;
		if (newRole === u.role) return;
		try {
			await adminUpdateUser(u.user_id, { role: newRole });
		} catch (e) {
			error = e instanceof ApiError ? e.message : 'Не удалось изменить роль';
		} finally {
			await load();
		}
	}

	async function changeStatus(u: UserResponse, event: Event) {
		const target = event.currentTarget as HTMLSelectElement;
		const newStatus = target.value as UserStatus;
		if (newStatus === u.status) return;
		if (newStatus === 'banned') {
			const label = u.login ?? u.user_id.slice(0, 8);
			if (!confirm(`Забанить пользователя ${label}?`)) {
				await load();
				return;
			}
		}
		try {
			await adminUpdateUser(u.user_id, { status: newStatus });
		} catch (e) {
			error = e instanceof ApiError ? e.message : 'Не удалось изменить статус';
		} finally {
			await load();
		}
	}

	function statusBadge(status: UserStatus): string {
		if (status === 'active') return 'border-green-700/40 bg-green-500/15 text-green-300';
		if (status === 'pending') return 'border-amber-700/40 bg-amber-500/15 text-amber-200';
		return 'border-red-700/40 bg-red-500/15 text-red-300';
	}

	function formatDate(iso: string): string {
		return new Date(iso).toLocaleString();
	}

	onMount(load);
</script>

<div class="page max-w-6xl space-y-6">
	<header class="flex flex-wrap items-start justify-between gap-4">
		<div>
			<h1 class="page-title">Пользователи</h1>
			<p class="page-sub">Управление ролями и статусами. Свою роль и статус менять нельзя.</p>
		</div>
		<button onclick={load} disabled={loading} class="btn-secondary btn-sm">
			{loading ? 'Загрузка…' : 'Обновить'}
		</button>
	</header>

	{#if error}
		<div class="alert-error">{error}</div>
	{/if}

	<section class="card space-y-4">
		<header class="section-head">
			<span
				class="section-icon border-indigo-700/30 bg-indigo-500/15 text-indigo-300"
				aria-hidden="true">👥</span
			>
			<div class="flex-1">
				<h2 class="section-title">Список аккаунтов</h2>
				<p class="section-sub">{users.length} в системе</p>
			</div>
		</header>

		<div class="table-wrap">
			<table class="data-table">
				<thead>
					<tr>
						<th>Login</th>
						<th>Telegram</th>
						<th>Discord</th>
						<th>Role</th>
						<th>Status</th>
						<th>Создан</th>
					</tr>
				</thead>
				<tbody>
					{#each users as u (u.user_id)}
						<tr>
							<td class="font-mono">
								<div class="flex items-center gap-2">
									<span class="text-gray-100">{u.login ?? '—'}</span>
									{#if isSelf(u)}
										<span class="badge border-indigo-500/30 bg-indigo-500/20 text-indigo-300">ты</span>
									{/if}
								</div>
							</td>
							<td class="font-mono text-xs text-gray-400">{u.telegram_chat_id ?? '—'}</td>
							<td class="font-mono text-xs text-gray-400">{u.discord_user_id ?? '—'}</td>
							<td>
								<select
									class="input input-sm w-auto"
									value={u.role}
									onchange={(e) => changeRole(u, e)}
									disabled={isSelf(u)}
									title={isSelf(u) ? 'Нельзя менять собственную роль' : ''}
								>
									{#each ROLES as role (role)}
										<option value={role}>{role}</option>
									{/each}
								</select>
							</td>
							<td>
								<div class="flex items-center gap-2">
									<span class="badge {statusBadge(u.status)}">{u.status}</span>
									<select
										class="input input-sm w-auto"
										value={u.status}
										onchange={(e) => changeStatus(u, e)}
										disabled={isSelf(u)}
										title={isSelf(u) ? 'Нельзя менять собственный статус' : ''}
									>
										{#each STATUSES as status (status)}
											<option value={status}>{status}</option>
										{/each}
									</select>
								</div>
							</td>
							<td class="text-xs text-gray-400">{formatDate(u.created_at)}</td>
						</tr>
					{/each}
					{#if users.length === 0 && !loading}
						<tr>
							<td colspan="6" class="empty-state">Нет пользователей</td>
						</tr>
					{/if}
				</tbody>
			</table>
		</div>
	</section>
</div>
