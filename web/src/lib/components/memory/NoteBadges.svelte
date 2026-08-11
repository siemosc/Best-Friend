<script lang="ts">
	// Бейджи заметки: kind, субъект, не-active статус, маркеры профиля/журнала.
	import type { NoteView } from '$lib/types';
	import {
		BADGE_BASE,
		KIND_BADGE,
		KIND_LABELS,
		PIN_SECTION_LABELS,
		STATUS_BADGE,
		STATUS_LABELS,
		SUBJECT_BADGE,
		SUBJECT_LABELS,
	} from './labels';

	const { note }: { note: NoteView } = $props();
</script>

<span class="{BADGE_BASE} {KIND_BADGE[note.kind]}">{KIND_LABELS[note.kind]}</span>
{#if note.subject}
	<span class="{BADGE_BASE} {SUBJECT_BADGE[note.subject]}">
		{SUBJECT_LABELS[note.subject]}
	</span>
{/if}
{#if note.status !== 'active'}
	<span class="{BADGE_BASE} {STATUS_BADGE[note.status]}">
		{STATUS_LABELS[note.status]}
	</span>
{/if}
{#if note.pinned}
	<span
		class="{BADGE_BASE} bg-yellow-500/10 text-yellow-300 border-yellow-800/40"
		title="В постоянном профиле"
	>
		📌 {note.pin_section ? PIN_SECTION_LABELS[note.pin_section] : 'профиль'}
	</span>
{/if}
{#if note.in_journal}
	<span
		class="{BADGE_BASE} bg-gray-700/40 text-gray-300 border-gray-700"
		title="В журнале (постоянный контекст)"
	>
		📓 журнал
	</span>
{/if}
