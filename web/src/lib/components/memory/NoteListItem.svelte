<script lang="ts">
	// Кликабельная строка заметки для списков (Заметки, Контекст, выдача поиска).
	import type { NoteView } from '$lib/types';
	import NoteBadges from './NoteBadges.svelte';
	import { formatDateTime } from './labels';

	const {
		note,
		onOpen,
	}: { note: NoteView; onOpen: (note: NoteView) => void } = $props();
</script>

<button
	type="button"
	onclick={() => onOpen(note)}
	class="w-full rounded-lg border border-gray-800 bg-gray-900/40 px-3 py-2.5 text-left transition-colors hover:border-gray-700 hover:bg-gray-800/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500/40"
>
	<p
		class="line-clamp-2 text-sm leading-snug {note.status === 'superseded'
			? 'text-gray-500 line-through'
			: 'text-gray-100'}"
	>
		{note.content}
	</p>
	<div class="mt-1.5 flex flex-wrap items-center gap-1.5">
		<NoteBadges {note} />
		{#each note.entities as entity (entity.id)}
			<span class="rounded bg-gray-800/80 px-1.5 py-0.5 font-mono text-[11px] text-gray-400">
				@{entity.name}
			</span>
		{/each}
		<span class="ml-auto whitespace-nowrap text-[11px] text-gray-500">
			{formatDateTime(note.observed_at)}
		</span>
	</div>
</button>
