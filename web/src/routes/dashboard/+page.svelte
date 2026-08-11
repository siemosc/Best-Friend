<script lang="ts">
	import { onDestroy, onMount } from 'svelte';
	import { ApiError, getDashboardHealth } from '$lib/api';
	import type {
		DashboardHealthSnapshot,
		ServiceHealth,
		ServiceHealthStatus,
	} from '$lib/types';

	const POLL_MS = 5000;
	const MCP_PORT_MIN = 8020;

	let snapshot = $state<DashboardHealthSnapshot | null>(null);
	let error: string | null = $state(null);
	let loading = $state(true);
	let refreshing = $state(false);
	let timer: ReturnType<typeof setInterval> | null = null;

	function servicePort(url: string): number {
		const match = url.match(/:(\d+)(?:\/|$)/);
		return match ? Number(match[1]) : 0;
	}

	let coreServices: ServiceHealth[] = $derived(
		snapshot === null
			? []
			: snapshot.services.filter((s) => servicePort(s.url) < MCP_PORT_MIN),
	);
	let mcpServices: ServiceHealth[] = $derived(
		snapshot === null
			? []
			: snapshot.services.filter((s) => servicePort(s.url) >= MCP_PORT_MIN),
	);

	let healthyCount = $derived(
		snapshot === null
			? 0
			: snapshot.services.filter((s) => s.status === 'healthy').length,
	);
	let totalCount = $derived(snapshot?.services.length ?? 0);

	async function refresh() {
		refreshing = true;
		try {
			snapshot = await getDashboardHealth();
			error = null;
		} catch (e) {
			error = e instanceof ApiError ? e.message : 'Не удалось получить health';
		} finally {
			refreshing = false;
			loading = false;
		}
	}

	function statusClass(status: ServiceHealthStatus): string {
		if (status === 'healthy')
			return 'border-green-700/40 bg-green-500/[0.07] hover:bg-green-500/10';
		if (status === 'timeout')
			return 'border-amber-700/40 bg-amber-500/[0.07] hover:bg-amber-500/10';
		return 'border-red-700/40 bg-red-500/[0.07] hover:bg-red-500/10';
	}

	function dotColor(status: ServiceHealthStatus): string {
		if (status === 'healthy') return 'bg-green-400';
		if (status === 'timeout') return 'bg-amber-400';
		return 'bg-red-400';
	}

	function formatTime(iso: string): string {
		return new Date(iso).toLocaleTimeString();
	}

	onMount(() => {
		refresh();
		timer = setInterval(refresh, POLL_MS);
	});
	onDestroy(() => {
		if (timer) clearInterval(timer);
	});
</script>

<div class="page max-w-6xl space-y-6">
	<header class="flex flex-wrap items-start justify-between gap-4">
		<div>
			<h1 class="page-title">Dashboard</h1>
			<p class="page-sub">
				Состояние сервисов и ссылка на Langfuse.
				{#if snapshot}
					Обновлено {formatTime(snapshot.fetched_at)} · авто-опрос каждые {POLL_MS /
						1000}с.
				{/if}
			</p>
		</div>
		<div class="flex items-center gap-2">
			{#if snapshot}
				<span
					class="hidden items-center gap-2 rounded-lg border border-gray-800 bg-gray-900/60 px-3 py-2 text-sm text-gray-300 sm:inline-flex"
					title="Сервисов в статусе healthy"
				>
					<span class="relative flex h-2 w-2">
						<span
							class="absolute inline-flex h-full w-full animate-ping rounded-full opacity-60
								{healthyCount === totalCount ? 'bg-green-400' : 'bg-amber-400'}"
						></span>
						<span
							class="relative inline-flex h-2 w-2 rounded-full
								{healthyCount === totalCount ? 'bg-green-400' : 'bg-amber-400'}"
						></span>
					</span>
					{healthyCount}/{totalCount} здоровы
				</span>
			{/if}
			<button onclick={refresh} disabled={refreshing} class="btn-secondary btn-sm">
				{refreshing ? 'Обновляем…' : 'Обновить'}
			</button>
			{#if snapshot}
				<a
					href={snapshot.links.langfuse_url}
					target="_blank"
					rel="noopener noreferrer"
					class="btn-primary btn-sm"
				>
					Langfuse ↗
				</a>
			{/if}
		</div>
	</header>

	{#if error}
		<div class="alert-error">{error}</div>
	{/if}

	{#if loading && !snapshot}
		<p class="empty-state">Загрузка…</p>
	{:else if snapshot}
		{#snippet serviceCard(s: ServiceHealth)}
			<div
				class="flex items-start justify-between gap-3 rounded-lg border p-4 transition-colors {statusClass(
					s.status,
				)}"
				title={s.error ?? ''}
			>
				<div class="min-w-0">
					<div class="flex items-center gap-2">
						<span class="relative flex h-2.5 w-2.5 shrink-0">
							{#if s.status === 'healthy'}
								<span
									class="absolute inline-flex h-full w-full animate-ping rounded-full bg-green-400 opacity-60"
								></span>
							{/if}
							<span class="relative inline-flex h-2.5 w-2.5 rounded-full {dotColor(s.status)}"></span>
						</span>
						<span class="truncate font-medium text-gray-100">{s.name}</span>
					</div>
					<div class="mt-1 truncate font-mono text-xs text-gray-500">{s.url}</div>
					{#if s.error}
						<div class="mt-1 truncate text-xs text-red-300/80">{s.error}</div>
					{/if}
				</div>
				<div class="shrink-0 text-right">
					<div class="font-mono text-sm text-gray-300">
						{s.latency_ms !== null ? `${s.latency_ms}ms` : '—'}
					</div>
				</div>
			</div>
		{/snippet}

		<section class="card space-y-4">
			<header class="section-head">
				<span class="section-icon border-blue-700/30 bg-blue-500/15 text-blue-300" aria-hidden="true"
					>⚙️</span
				>
				<div class="flex-1">
					<h2 class="section-title">Core-сервисы</h2>
					<p class="section-sub">
						Ядро платформы · порты 8000–8019 · {coreServices.length} шт.
					</p>
				</div>
			</header>
			{#if coreServices.length === 0}
				<p class="text-xs text-gray-500">Нет сервисов в диапазоне.</p>
			{:else}
				<div class="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
					{#each coreServices as s (s.name)}
						{@render serviceCard(s)}
					{/each}
				</div>
			{/if}
		</section>

		<section class="card space-y-4">
			<header class="section-head">
				<span
					class="section-icon border-emerald-700/30 bg-emerald-500/15 text-emerald-300"
					aria-hidden="true">🔌</span
				>
				<div class="flex-1">
					<h2 class="section-title">MCP-агенты</h2>
					<p class="section-sub">
						Подключаемые инструменты · порты 8020+ · {mcpServices.length} шт.
					</p>
				</div>
			</header>
			{#if mcpServices.length === 0}
				<p class="text-xs text-gray-500">Нет сервисов в диапазоне.</p>
			{:else}
				<div class="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
					{#each mcpServices as s (s.name)}
						{@render serviceCard(s)}
					{/each}
				</div>
			{/if}
		</section>
	{/if}
</div>
