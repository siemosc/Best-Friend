<script lang="ts">
	import { onMount } from 'svelte';
	import {
		ApiError,
		createMcpConnection,
		deleteMcpConnection,
		deleteMcpSubscription,
		disconnectMcpOauth,
		discoverMcpPreview,
		listMcpConnections,
		listMyMcpServers,
		startMcpOauth,
		updateMcpConnection,
		upsertMcpSubscription,
	} from '$lib/api';
	import { user } from '$lib/stores/user';
	import type {
		CreateMcpConnectionRequest,
		DiscoverPreviewResponse,
		McpAuthType,
		McpConnectionView,
		McpOAuthStatus,
		McpServerSubscriptionView,
		UpdateMcpConnectionRequest,
	} from '$lib/types';

	const AUTH_TYPES: McpAuthType[] = ['none', 'bearer', 'oauth'];
	const OAUTH_DCR_HINT =
		'Пусто — сервер должен поддерживать динамическую регистрацию (DCR); для Google обязательны креды из Cloud Console.';

	function oauthStatusLabel(status: McpOAuthStatus): string {
		switch (status) {
			case 'connected':
				return 'Подключён';
			case 'expired':
				return 'Требует переподключения';
			case 'not_connected':
				return 'Не подключён';
		}
	}

	function oauthBadgeClass(status: McpOAuthStatus): string {
		switch (status) {
			case 'connected':
				return 'border-emerald-500/30 bg-emerald-500/20 text-emerald-300';
			case 'expired':
				return 'border-amber-500/30 bg-amber-500/20 text-amber-300';
			case 'not_connected':
				return 'border-gray-600/40 bg-gray-700/40 text-gray-300';
		}
	}

	let isAdmin = $derived($user?.role === 'admin');

	let myServers = $state<McpServerSubscriptionView[]>([]);
	let connections = $state<McpConnectionView[]>([]);
	let loading = $state(true);
	let error = $state<string | null>(null);
	// баннер возврата из OAuth-цикла (?oauth_connected / ?oauth_error)
	let notice = $state<{ kind: 'success' | 'error'; text: string } | null>(null);

	// create-форма (admin)
	let createForm = $state({
		name: '',
		url: '',
		auth_type: 'none' as McpAuthType,
		is_public: false,
		timeout_s: 30,
		supports_parallel_tool_calls: true,
		preview_token: '', // только для проверки ad-hoc, в connection не сохраняется
		oauth_client_id: '', // только при auth_type='oauth'; пусто = DCR
		oauth_client_secret: '',
	});
	let createError = $state<string | null>(null);
	let creating = $state(false);

	// редактирование OAuth-кред существующего подключения (admin)
	let oauthEditFor = $state<string | null>(null);
	let oauthEditForm = $state({ client_id: '', client_secret: '' });
	let oauthEditError = $state<string | null>(null);
	let oauthEditSaving = $state(false);

	// preview (общий, on-demand): previewFor = 'create' | connection_id
	let previewFor = $state<string | null>(null);
	let previewResult = $state<DiscoverPreviewResponse | null>(null);
	let previewError = $state<string | null>(null);
	let previewing = $state(false);

	// subscription drafts (per server)
	type SubDraft = {
		enabled: boolean;
		auth_token: string;
		disabled_tools: string; // comma-separated
		timeout_s: string; // пусто = дефолт сервера
	};
	let drafts = $state<Record<string, SubDraft>>({});
	let subError = $state<string | null>(null);

	function draftFromServer(s: McpServerSubscriptionView): SubDraft {
		const sub = s.subscription;
		return {
			enabled: sub?.enabled ?? true,
			auth_token: sub?.auth_token ?? '',
			disabled_tools: (sub?.disabled_tools ?? []).join(', '),
			timeout_s: sub?.timeout_s != null ? String(sub.timeout_s) : '',
		};
	}

	async function load() {
		error = null;
		try {
			myServers = await listMyMcpServers();
			drafts = Object.fromEntries(
				myServers.map((s) => [s.connection_id, draftFromServer(s)]),
			);
			if (isAdmin) connections = await listMcpConnections();
		} catch (e) {
			error = e instanceof ApiError ? e.message : 'Не удалось загрузить';
		} finally {
			loading = false;
		}
	}

	async function handleCreate() {
		createError = null;
		creating = true;
		try {
			const payload: CreateMcpConnectionRequest = {
				name: createForm.name.trim(),
				url: createForm.url.trim(),
				auth_type: createForm.auth_type,
				is_public: createForm.is_public,
				timeout_s: createForm.timeout_s,
				supports_parallel_tool_calls: createForm.supports_parallel_tool_calls,
			};
			// креды передаём только для oauth; пустые опускаем (= DCR)
			if (createForm.auth_type === 'oauth') {
				const clientId = createForm.oauth_client_id.trim();
				const clientSecret = createForm.oauth_client_secret.trim();
				if (clientId !== '') payload.oauth_client_id = clientId;
				if (clientSecret !== '') payload.oauth_client_secret = clientSecret;
			}
			await createMcpConnection(payload);
			createForm = {
				name: '',
				url: '',
				auth_type: 'none',
				is_public: false,
				timeout_s: 30,
				supports_parallel_tool_calls: true,
				preview_token: '',
				oauth_client_id: '',
				oauth_client_secret: '',
			};
			previewFor = null;
			await load();
		} catch (e) {
			createError = e instanceof ApiError ? e.message : 'Не удалось создать';
		} finally {
			creating = false;
		}
	}

	function startOauthEdit(c: McpConnectionView) {
		oauthEditFor = c.connection_id;
		oauthEditForm = { client_id: c.oauth_client_id ?? '', client_secret: '' };
		oauthEditError = null;
	}

	async function saveOauthClient(c: McpConnectionView) {
		oauthEditError = null;
		oauthEditSaving = true;
		try {
			const patch: UpdateMcpConnectionRequest = {
				oauth_client_id: oauthEditForm.client_id.trim(),
			};
			// секрет write-only: пустое поле = оставить сохранённый
			const secret = oauthEditForm.client_secret.trim();
			if (secret !== '') patch.oauth_client_secret = secret;
			await updateMcpConnection(c.connection_id, patch);
			oauthEditFor = null;
			await load();
		} catch (e) {
			oauthEditError = e instanceof ApiError ? e.message : 'Не удалось сохранить';
		} finally {
			oauthEditSaving = false;
		}
	}

	async function handleDeleteConnection(c: McpConnectionView) {
		if (!confirm(`Удалить сервер «${c.name}»? Подписки юзеров тоже исчезнут.`)) {
			return;
		}
		error = null;
		try {
			await deleteMcpConnection(c.connection_id);
			await load();
		} catch (e) {
			error = e instanceof ApiError ? e.message : 'Не удалось удалить';
		}
	}

	async function toggleParallel(c: McpConnectionView, event: Event) {
		const target = event.currentTarget as HTMLInputElement;
		error = null;
		try {
			await updateMcpConnection(c.connection_id, {
				supports_parallel_tool_calls: target.checked,
			});
		} catch (e) {
			error = e instanceof ApiError ? e.message : 'Не удалось обновить';
		} finally {
			await load();
		}
	}

	async function previewCreate() {
		previewFor = 'create';
		previewResult = null;
		previewError = null;
		previewing = true;
		try {
			previewResult = await discoverMcpPreview({
				url: createForm.url.trim(),
				auth_type: createForm.auth_type,
				auth_token:
					createForm.auth_type === 'bearer'
						? createForm.preview_token.trim() || null
						: undefined,
			});
		} catch (e) {
			previewError = e instanceof ApiError ? e.message : 'Проверка не удалась';
		} finally {
			previewing = false;
		}
	}

	async function previewServer(connectionId: string) {
		previewFor = connectionId;
		previewResult = null;
		previewError = null;
		previewing = true;
		try {
			// токен берётся сервером из подписки юзера; url — из доверенной записи
			previewResult = await discoverMcpPreview({ connection_id: connectionId });
		} catch (e) {
			previewError = e instanceof ApiError ? e.message : 'Проверка не удалась';
		} finally {
			previewing = false;
		}
	}

	async function saveSubscription(connectionId: string) {
		subError = null;
		const d = drafts[connectionId];
		try {
			await upsertMcpSubscription(connectionId, {
				enabled: d.enabled,
				auth_token: d.auth_token.trim() === '' ? null : d.auth_token.trim(),
				disabled_tools: d.disabled_tools
					.split(',')
					.map((t) => t.trim())
					.filter((t) => t !== ''),
				timeout_s: d.timeout_s.trim() === '' ? null : Number(d.timeout_s),
			});
			await load();
		} catch (e) {
			subError = e instanceof ApiError ? e.message : 'Не удалось сохранить';
		}
	}

	async function removeSubscription(connectionId: string) {
		subError = null;
		try {
			await deleteMcpSubscription(connectionId);
			await load();
		} catch (e) {
			subError = e instanceof ApiError ? e.message : 'Не удалось отписаться';
		}
	}

	async function connectOauth(connectionId: string) {
		subError = null;
		try {
			const { authorization_url } = await startMcpOauth(connectionId);
			// уходим на authorization endpoint; возврат — на /mcp с query-параметрами
			window.location.href = authorization_url;
		} catch (e) {
			subError = e instanceof ApiError ? e.message : 'Не удалось начать подключение';
		}
	}

	async function disconnectOauth(connectionId: string) {
		subError = null;
		try {
			await disconnectMcpOauth(connectionId);
			await load();
		} catch (e) {
			subError = e instanceof ApiError ? e.message : 'Не удалось отключить';
		}
	}

	// Разбирает ?oauth_connected / ?oauth_error после возврата из OAuth-цикла и чистит query.
	function readOauthReturn() {
		const params = new URLSearchParams(window.location.search);
		const connected = params.get('oauth_connected');
		const errored = params.get('oauth_error');
		if (connected !== null) {
			notice = { kind: 'success', text: `Сервер «${connected}» подключён.` };
		} else if (errored !== null) {
			notice = { kind: 'error', text: `Не удалось подключить OAuth: ${errored}` };
		}
		if (connected !== null || errored !== null) {
			params.delete('oauth_connected');
			params.delete('oauth_error');
			const qs = params.toString();
			history.replaceState(
				null,
				'',
				window.location.pathname + (qs ? `?${qs}` : ''),
			);
		}
	}

	onMount(() => {
		readOauthReturn();
		load();
	});
</script>

<div class="page max-w-6xl space-y-6">
	<header class="flex flex-wrap items-start justify-between gap-4">
		<div>
			<h1 class="page-title">MCP-подключения</h1>
			<p class="page-sub">Внешние MCP-серверы и твой персональный доступ к ним.</p>
		</div>
		<button onclick={load} disabled={loading} class="btn-secondary btn-sm">
			{loading ? 'Загрузка…' : 'Обновить'}
		</button>
	</header>

	{#if error}
		<div class="alert-error">{error}</div>
	{/if}

	{#if notice}
		<div class={notice.kind === 'success' ? 'alert-success' : 'alert-error'}>
			{notice.text}
		</div>
	{/if}

	{#if isAdmin}
		<section class="card space-y-4">
			<header class="section-head">
				<span
					class="section-icon border-emerald-700/30 bg-emerald-500/15 text-emerald-300"
					aria-hidden="true">🖥️</span
				>
				<div class="flex-1">
					<h2 class="section-title">Серверы (admin)</h2>
					<p class="section-sub">
						Определения серверов, общие для всех. Public-сервер обязан быть без авторизации.
					</p>
				</div>
			</header>

			<!-- create-форма -->
			<div class="space-y-3 rounded-lg border border-gray-800 bg-gray-950/40 p-4">
				<div class="grid grid-cols-1 gap-3 sm:grid-cols-2">
					<label class="block">
						<span class="field-label">Имя</span>
						<input bind:value={createForm.name} placeholder="websearch" class="input input-sm" />
					</label>
					<label class="block">
						<span class="field-label">URL</span>
						<input
							bind:value={createForm.url}
							placeholder="https://host/mcp"
							class="input input-sm font-mono"
						/>
					</label>
					<label class="block">
						<span class="field-label">Авторизация</span>
						<select bind:value={createForm.auth_type} class="input input-sm">
							{#each AUTH_TYPES as a (a)}
								<option value={a}>{a}</option>
							{/each}
						</select>
					</label>
					<label class="block">
						<span class="field-label">Таймаут, с</span>
						<input
							type="number"
							min="1"
							max="300"
							bind:value={createForm.timeout_s}
							class="input input-sm"
						/>
					</label>
				</div>
				<div class="flex flex-wrap items-center gap-4">
					<label class="flex items-center gap-2 text-sm">
						<input type="checkbox" bind:checked={createForm.is_public} class="accent-indigo-600" />
						<span>Public (виден всем, только без авторизации)</span>
					</label>
					<label
						class="flex items-center gap-2 text-sm"
						title="Сервер держит несколько одновременных вызовов. Выключи, если он принимает запросы по одному."
					>
						<input
							type="checkbox"
							bind:checked={createForm.supports_parallel_tool_calls}
							class="accent-indigo-600"
						/>
						<span>Параллельные вызовы</span>
					</label>
					{#if createForm.auth_type === 'bearer'}
						<label class="flex min-w-[200px] flex-1 items-center gap-2 text-sm">
							<span class="whitespace-nowrap text-gray-400">Токен для проверки</span>
							<input
								bind:value={createForm.preview_token}
								placeholder="не сохраняется"
								class="input input-sm flex-1"
							/>
						</label>
					{/if}
				</div>
				{#if createForm.auth_type === 'oauth'}
					<div class="grid grid-cols-1 gap-3 sm:grid-cols-2">
						<label class="block">
							<span class="field-label">Client ID</span>
							<input
								bind:value={createForm.oauth_client_id}
								placeholder="пусто = DCR"
								class="input input-sm font-mono"
							/>
						</label>
						<label class="block">
							<span class="field-label">Client Secret</span>
							<input
								bind:value={createForm.oauth_client_secret}
								placeholder="пусто = DCR"
								class="input input-sm font-mono"
							/>
						</label>
						<p class="text-xs text-gray-500 sm:col-span-2">{OAUTH_DCR_HINT}</p>
					</div>
				{/if}
				{#if createError}
					<div class="alert-error">{createError}</div>
				{/if}
				<div class="flex items-center gap-2">
					<button
						onclick={handleCreate}
						disabled={creating || createForm.name.trim() === '' || createForm.url.trim() === ''}
						class="btn-primary btn-sm"
					>
						{creating ? 'Создаю…' : 'Создать'}
					</button>
					<button
						onclick={previewCreate}
						disabled={previewing || createForm.url.trim() === ''}
						class="btn-secondary btn-sm"
					>
						Проверить
					</button>
				</div>
				{#if previewFor === 'create'}
					{@render previewBlock()}
				{/if}
			</div>

			<!-- таблица серверов -->
			<div class="table-wrap">
				<table class="data-table">
					<thead>
						<tr>
							<th>Имя</th>
							<th>URL</th>
							<th>Auth</th>
							<th>Public</th>
							<th>Таймаут</th>
							<th>Параллельно</th>
							<th class="text-right"></th>
						</tr>
					</thead>
					<tbody>
						{#each connections as c (c.connection_id)}
							<tr>
								<td>
									<span class="text-gray-100">{c.name}</span>
									{#if c.is_system}
										<span class="badge ml-1 border-amber-500/30 bg-amber-500/20 text-amber-300">sys</span
										>
									{/if}
								</td>
								<td class="max-w-[260px] truncate font-mono text-xs text-gray-400">{c.url}</td>
								<td class="text-gray-400">{c.auth_type}</td>
								<td>{c.is_public ? 'да' : '—'}</td>
								<td class="text-gray-400">{c.timeout_s}s</td>
								<td>
									<input
										type="checkbox"
										checked={c.supports_parallel_tool_calls}
										onchange={(e) => toggleParallel(c, e)}
										class="accent-indigo-600"
										title="Сервер держит несколько одновременных вызовов"
									/>
								</td>
								<td class="text-right">
									<div class="flex items-center justify-end gap-2">
										{#if c.auth_type === 'oauth'}
											<button
												onclick={() => startOauthEdit(c)}
												class="btn-secondary btn-sm"
											>
												OAuth-креды
											</button>
										{/if}
										<button
											onclick={() => handleDeleteConnection(c)}
											disabled={c.is_system}
											title={c.is_system ? 'Системный сервер удалить нельзя' : 'Удалить'}
											class="btn-danger btn-sm"
										>
											Удалить
										</button>
									</div>
								</td>
							</tr>
							{#if oauthEditFor === c.connection_id}
								<tr>
									<td colspan="7">
										<div class="space-y-2">
											<div class="grid grid-cols-1 gap-3 sm:grid-cols-2">
												<label class="block">
													<span class="field-label">Client ID</span>
													<input
														bind:value={oauthEditForm.client_id}
														placeholder="пусто = DCR"
														class="input input-sm font-mono"
													/>
												</label>
												<label class="block">
													<span class="field-label">Client Secret</span>
													<input
														bind:value={oauthEditForm.client_secret}
														placeholder={c.oauth_client_id ? '(сохранён)' : 'пусто = DCR'}
														class="input input-sm font-mono"
													/>
												</label>
											</div>
											<p class="text-xs text-gray-500">{OAUTH_DCR_HINT}</p>
											{#if oauthEditError}
												<div class="alert-error">{oauthEditError}</div>
											{/if}
											<div class="flex items-center gap-2">
												<button
													onclick={() => saveOauthClient(c)}
													disabled={oauthEditSaving}
													class="btn-primary btn-sm"
												>
													{oauthEditSaving ? 'Сохраняю…' : 'Сохранить'}
												</button>
												<button
													onclick={() => (oauthEditFor = null)}
													class="btn-secondary btn-sm"
												>
													Отмена
												</button>
											</div>
										</div>
									</td>
								</tr>
							{/if}
						{/each}
						{#if connections.length === 0 && !loading}
							<tr><td colspan="7" class="empty-state">Серверов нет</td></tr>
						{/if}
					</tbody>
				</table>
			</div>
		</section>
	{/if}

	<section class="card space-y-4">
		<header class="section-head">
			<span
				class="section-icon border-indigo-700/30 bg-indigo-500/15 text-indigo-300"
				aria-hidden="true">🔌</span
			>
			<div class="flex-1">
				<h2 class="section-title">Мои подключения</h2>
				<p class="section-sub">
					Public-серверы и те, на которые ты подписан. Таймаут пустой = дефолт сервера.
				</p>
			</div>
		</header>

		{#if subError}
			<div class="alert-error">{subError}</div>
		{/if}

		{#each myServers as s (s.connection_id)}
			<div class="space-y-3 rounded-lg border border-gray-800 bg-gray-950/40 p-4">
				<div class="flex flex-wrap items-start justify-between gap-3">
					<div>
						<div class="flex items-center gap-2">
							<span class="font-medium text-gray-100">{s.name}</span>
							<span
								class="badge {s.is_public
									? 'border-emerald-500/30 bg-emerald-500/20 text-emerald-300'
									: 'border-gray-600/40 bg-gray-700/40 text-gray-300'}"
							>
								{s.is_public ? 'public' : 'private'}
							</span>
							{#if s.subscription}
								<span class="badge border-indigo-500/30 bg-indigo-500/20 text-indigo-300"
									>подписан</span
								>
							{/if}
							{#if s.auth_type === 'oauth'}
								{@const st = s.oauth_status ?? 'not_connected'}
								<span class="badge {oauthBadgeClass(st)}">{oauthStatusLabel(st)}</span>
							{/if}
						</div>
						<div class="mt-0.5 font-mono text-xs text-gray-500">{s.url}</div>
					</div>
					<div class="flex items-center gap-2">
						{#if s.auth_type === 'oauth'}
							{@const st = s.oauth_status ?? 'not_connected'}
							{#if st === 'not_connected'}
								<button
									onclick={() => connectOauth(s.connection_id)}
									class="btn-primary btn-sm"
								>
									Подключить
								</button>
							{:else if st === 'expired'}
								<button
									onclick={() => connectOauth(s.connection_id)}
									class="btn-primary btn-sm"
								>
									Переподключить
								</button>
							{/if}
							{#if st === 'connected' || st === 'expired'}
								<button
									onclick={() => disconnectOauth(s.connection_id)}
									class="btn-secondary btn-sm"
								>
									Отключить
								</button>
							{/if}
						{/if}
						<button
							onclick={() => previewServer(s.connection_id)}
							disabled={previewing}
							class="btn-secondary btn-sm"
						>
							Проверить
						</button>
					</div>
				</div>

				{#if drafts[s.connection_id]}
					<div class="grid grid-cols-1 gap-3 sm:grid-cols-2">
						<label class="flex items-center gap-2 text-sm">
							<input
								type="checkbox"
								bind:checked={drafts[s.connection_id].enabled}
								class="accent-indigo-600"
							/>
							<span>Включён</span>
						</label>
						<label class="block">
							<span class="field-label">Таймаут, с (пусто = {s.timeout_s})</span>
							<input
								bind:value={drafts[s.connection_id].timeout_s}
								placeholder={String(s.timeout_s)}
								class="input input-sm"
							/>
						</label>
						{#if s.auth_type === 'bearer'}
							<label class="block sm:col-span-2">
								<span class="field-label">Токен</span>
								<input
									bind:value={drafts[s.connection_id].auth_token}
									placeholder="bearer-токен сервера"
									class="input input-sm font-mono"
								/>
							</label>
						{/if}
						<label class="block sm:col-span-2">
							<span class="field-label">Отключённые тулзы (через запятую)</span>
							<input
								bind:value={drafts[s.connection_id].disabled_tools}
								placeholder="tool_a, tool_b"
								class="input input-sm font-mono"
							/>
						</label>
					</div>
					<div class="flex items-center gap-2">
						<button onclick={() => saveSubscription(s.connection_id)} class="btn-primary btn-sm">
							Сохранить
						</button>
						{#if s.subscription}
							<button
								onclick={() => removeSubscription(s.connection_id)}
								class="btn-secondary btn-sm"
							>
								Сбросить
							</button>
						{/if}
					</div>
				{/if}

				{#if previewFor === s.connection_id}
					{@render previewBlock()}
				{/if}
			</div>
		{/each}
		{#if myServers.length === 0 && !loading}
			<div class="empty-state">Доступных серверов нет</div>
		{/if}
	</section>
</div>

{#snippet previewBlock()}
	<div class="space-y-2 rounded-lg border border-gray-800 bg-gray-950/60 p-3 text-sm">
		{#if previewing}
			<div class="flex items-center gap-2 text-gray-400">
				<span class="spinner h-3.5 w-3.5"></span>
				Проверяю…
			</div>
		{:else if previewError}
			<div class="text-red-300">{previewError}</div>
		{:else if previewResult}
			{#if previewResult.failure}
				<div class="text-amber-300">
					⚠️ {previewResult.failure.kind}: {previewResult.failure.message}
				</div>
			{:else}
				{#if previewResult.instructions}
					<div class="whitespace-pre-wrap text-gray-300">{previewResult.instructions}</div>
				{/if}
				<div class="text-xs text-gray-400">Тулзов: {previewResult.tools.length}</div>
				<ul class="space-y-1">
					{#each previewResult.tools as t (t.name)}
						<li class="text-xs">
							<span class="font-mono text-emerald-300">{t.name}</span>
							{#if t.description}<span class="text-gray-500"> — {t.description}</span>{/if}
						</li>
					{/each}
				</ul>
			{/if}
		{/if}
	</div>
{/snippet}
