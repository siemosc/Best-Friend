<script lang="ts">
	// Секция «Сущности»: реестр с алиасами и числом активных заметок.
	// Клик по сущности уводит в «Заметки» с фильтром по ней.
	import { ApiError, listMemoryEntities } from '$lib/api';
	import type { EntityView } from '$lib/types';

	const {
		userId,
		onPickEntity,
		refreshKey,
	}: {
		userId: string;
		onPickEntity: (entity: EntityView) => void;
		refreshKey: number;
	} = $props();

	let entities: EntityView[] = $state([]);
	let loading = $state(true);
	let error: string | null = $state(null);

	async function load() {
		loading = true;
		error = null;
		try {
			entities = await listMemoryEntities(userId);
		} catch (e) {
			error = e instanceof ApiError ? e.message : 'Не удалось загрузить сущности';
		} finally {
			loading = false;
		}
	}

	$effect(() => {
		void userId;
		void refreshKey;
		void load();
	});

	/** Алиасы без дубля каноничного имени (оно и так в первой колонке). */
	function extraAliases(entity: EntityView): string[] {
		return entity.aliases.filter(
			(alias) => alias.toLowerCase() !== entity.canonical_name.toLowerCase(),
		);
	}
</script>

{#if error}
	<div class="alert-error">{error}</div>
{:else if loading}
	<p class="empty-state">Загрузка…</p>
{:else if entities.length === 0}
	<p class="empty-state">
		Реестр пуст — сущности появляются, когда Observer находит в диалоге людей,
		проекты и темы.
	</p>
{:else}
	<div class="table-wrap">
		<table class="data-table">
			<thead>
				<tr>
					<th>Сущность</th>
					<th>Алиасы</th>
					<th class="text-right">Заметок</th>
				</tr>
			</thead>
			<tbody>
				{#each entities as entity (entity.id)}
					<tr
						class="cursor-pointer"
						onclick={() => onPickEntity(entity)}
						title="Показать заметки сущности"
					>
						<td class="font-mono text-gray-100">@{entity.canonical_name}</td>
						<td>
							<div class="flex flex-wrap items-center gap-1">
								{#each extraAliases(entity) as alias (alias)}
									<span class="rounded bg-gray-800/80 px-1.5 py-0.5 text-xs text-gray-400">
										{alias}
									</span>
								{/each}
							</div>
						</td>
						<td class="text-right text-gray-300">{entity.notes_count}</td>
					</tr>
				{/each}
			</tbody>
		</table>
	</div>
{/if}
