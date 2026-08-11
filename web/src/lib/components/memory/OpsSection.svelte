<script lang="ts">
	// Секция «История»: лента memory_ops — почему агент это запомнил/забыл.
	import { ApiError, listMemoryOps } from '$lib/api';
	import type { MemoryOpView, MemoryPipeline } from '$lib/types';
	import {
		BADGE_BASE,
		formatDateTime,
		OP_LABELS,
		PIPELINE_BADGE,
		PIPELINE_LABELS,
	} from './labels';

	const {
		userId,
		refreshKey,
	}: { userId: string; refreshKey: number } = $props();

	const ALL_PIPELINES = Object.keys(PIPELINE_LABELS) as MemoryPipeline[];
	const PAGE_LIMIT = 50;

	let pipelines: MemoryPipeline[] = $state([]);
	let offset = $state(0);
	let items: MemoryOpView[] = $state([]);
	let total = $state(0);
	let loading = $state(true);
	let error: string | null = $state(null);

	async function load() {
		loading = true;
		error = null;
		try {
			const page = await listMemoryOps(userId, {
				pipelines: pipelines.length ? pipelines : undefined,
				limit: PAGE_LIMIT,
				offset,
			});
			items = page.items;
			total = page.total;
		} catch (e) {
			error = e instanceof ApiError ? e.message : 'Не удалось загрузить историю';
		} finally {
			loading = false;
		}
	}

	$effect(() => {
		void userId;
		void refreshKey;
		void pipelines;
		void offset;
		void load();
	});

	function togglePipeline(pipeline: MemoryPipeline) {
		offset = 0;
		pipelines = pipelines.includes(pipeline)
			? pipelines.filter((item) => item !== pipeline)
			: [...pipelines, pipeline];
	}

	const pageStart = $derived(total === 0 ? 0 : offset + 1);
	const pageEnd = $derived(Math.min(offset + PAGE_LIMIT, total));
</script>

<div class="space-y-3">
	<div class="flex flex-wrap items-center gap-1.5 text-xs">
		{#each ALL_PIPELINES as pipeline (pipeline)}
			<button
				type="button"
				onclick={() => togglePipeline(pipeline)}
				class="chip {pipelines.includes(pipeline)
					? 'border-blue-500 bg-blue-600/30 text-blue-200'
					: 'chip-off'}"
			>
				{PIPELINE_LABELS[pipeline]}
			</button>
		{/each}
	</div>

	{#if error}
		<div class="alert-error">{error}</div>
	{:else if loading}
		<p class="empty-state">Загрузка…</p>
	{:else if items.length === 0}
		<p class="empty-state">Операций пока нет.</p>
	{:else}
		<ul class="space-y-1.5">
			{#each items as op (op.id)}
				<li class="rounded-lg border border-gray-800 bg-gray-900/40 px-3 py-2">
					<div class="flex flex-wrap items-baseline gap-2 text-xs">
						<span class="whitespace-nowrap text-gray-500">
							{formatDateTime(op.created_at)}
						</span>
						<span class="{BADGE_BASE} {PIPELINE_BADGE[op.pipeline]}">
							{PIPELINE_LABELS[op.pipeline]}
						</span>
						<span class="text-gray-200">{OP_LABELS[op.op] ?? op.op}</span>
						{#if op.detail}
							<span class="text-gray-500">· {op.detail}</span>
						{/if}
					</div>
					{#if op.note_content}
						<p class="mt-1 line-clamp-2 text-xs text-gray-400">{op.note_content}</p>
					{/if}
					{#if op.target_note_content}
						<p class="mt-0.5 line-clamp-1 text-xs text-gray-500">
							← {op.target_note_content}
						</p>
					{/if}
				</li>
			{/each}
		</ul>
		{#if total > PAGE_LIMIT}
			<div class="flex items-center justify-between pt-1 text-sm text-gray-400">
				<button
					type="button"
					disabled={offset === 0}
					onclick={() => (offset = Math.max(0, offset - PAGE_LIMIT))}
					class="btn-secondary btn-sm"
				>
					← Назад
				</button>
				<span>{pageStart}–{pageEnd} из {total}</span>
				<button
					type="button"
					disabled={pageEnd >= total}
					onclick={() => (offset = offset + PAGE_LIMIT)}
					class="btn-secondary btn-sm"
				>
					Вперёд →
				</button>
			</div>
		{/if}
	{/if}
</div>
