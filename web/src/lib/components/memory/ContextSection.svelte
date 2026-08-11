<script lang="ts">
	// Секция «Контекст»: что модель держит в промпте постоянно — профиль по
	// pin-секциям и журнал. Порядок отдаёт backend теми же читалками, что
	// промпт-рендер; здесь только группировка профиля по секциям без пересортировки.
	import { ApiError, getMemoryContext } from '$lib/api';
	import type { NoteView, PinSection } from '$lib/types';
	import NoteListItem from './NoteListItem.svelte';
	import { PIN_SECTION_LABELS } from './labels';

	const {
		userId,
		onOpen,
		refreshKey,
	}: {
		userId: string;
		onOpen: (note: NoteView) => void;
		refreshKey: number;
	} = $props();

	let profile: NoteView[] = $state([]);
	let journal: NoteView[] = $state([]);
	let loading = $state(true);
	let error: string | null = $state(null);

	async function load() {
		loading = true;
		error = null;
		try {
			const context = await getMemoryContext(userId);
			profile = context.profile;
			journal = context.journal;
		} catch (e) {
			error = e instanceof ApiError ? e.message : 'Не удалось загрузить контекст';
		} finally {
			loading = false;
		}
	}

	$effect(() => {
		void userId;
		void refreshKey;
		void load();
	});

	// Группировка стабильна: внутри секции порядок читалки сохраняется.
	const profileSections = $derived.by(() => {
		const groups = new Map<string, NoteView[]>();
		for (const note of profile) {
			const key = note.pin_section ?? 'other';
			const bucket = groups.get(key);
			if (bucket) bucket.push(note);
			else groups.set(key, [note]);
		}
		return [...groups.entries()];
	});

	function sectionTitle(key: string): string {
		return key in PIN_SECTION_LABELS
			? PIN_SECTION_LABELS[key as PinSection]
			: 'без секции';
	}
</script>

{#if error}
	<div class="alert-error">{error}</div>
{:else if loading}
	<p class="empty-state">Загрузка…</p>
{:else}
	<div class="grid items-start gap-4 lg:grid-cols-2">
		<section class="card space-y-3">
			<div>
				<h2 class="text-base font-medium text-gray-100">📌 Профиль</h2>
				<p class="section-sub">
					Закреплённые заметки — всегда в системном промпте, по секциям.
				</p>
			</div>
			{#if profile.length === 0}
				<p class="text-sm text-gray-500">Профиль пуст.</p>
			{:else}
				{#each profileSections as [sectionKey, notes] (sectionKey)}
					<div class="space-y-1.5">
						<h3 class="text-xs uppercase tracking-wider text-gray-500">
							{sectionTitle(sectionKey)}
						</h3>
						{#each notes as note (note.id)}
							<NoteListItem {note} {onOpen} />
						{/each}
					</div>
				{/each}
			{/if}
		</section>

		<section class="card space-y-3">
			<div>
				<h2 class="text-base font-medium text-gray-100">📓 Журнал</h2>
				<p class="section-sub">
					Рабочее множество свежих заметок — в промпте хронологически, вытесняется
					бюджетом.
				</p>
			</div>
			{#if journal.length === 0}
				<p class="text-sm text-gray-500">Журнал пуст.</p>
			{:else}
				<div class="space-y-1.5">
					{#each journal as note (note.id)}
						<NoteListItem {note} {onOpen} />
					{/each}
				</div>
			{/if}
		</section>
	</div>
{/if}
