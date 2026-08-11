<script lang="ts">
	// Вкладка «Память»: 4 секции (Заметки / Контекст / Сущности / История) +
	// панель заметки. Админ смотрит память любого пользователя (как /assistant).
	import { onMount } from 'svelte';
	import { getMemoryOverview, listUsers } from '$lib/api';
	import { user as currentUser } from '$lib/stores/user';
	import type {
		EntityView,
		MemoryOverviewResponse,
		NoteView,
		UserResponse,
	} from '$lib/types';
	import ContextSection from '$lib/components/memory/ContextSection.svelte';
	import EntitiesSection from '$lib/components/memory/EntitiesSection.svelte';
	import NoteDetails from '$lib/components/memory/NoteDetails.svelte';
	import NotesSection from '$lib/components/memory/NotesSection.svelte';
	import OpsSection from '$lib/components/memory/OpsSection.svelte';

	type Tab = 'notes' | 'context' | 'entities' | 'ops';

	const TABS: { id: Tab; label: string }[] = [
		{ id: 'notes', label: 'Заметки' },
		{ id: 'context', label: 'Контекст' },
		{ id: 'entities', label: 'Сущности' },
		{ id: 'ops', label: 'История' },
	];

	const isAdmin = $derived($currentUser?.role === 'admin');

	let selectedUserId = $state($currentUser?.user_id ?? '');
	let adminUsers: UserResponse[] = $state([]);
	let activeTab: Tab = $state('notes');
	// generic-форма: чтение в top-level $derived иначе сужает тип к null.
	let overview = $state<MemoryOverviewResponse | null>(null);
	let selectedNote: NoteView | null = $state(null);
	let entityFilter: EntityView | null = $state(null);
	// Инкремент после любой мутации — секции и счётчики перезагружаются.
	let refreshKey = $state(0);

	const activeNotesTotal = $derived(
		overview
			? Object.values(overview.by_kind).reduce((sum, count) => sum + count, 0)
			: 0,
	);

	async function loadOverview() {
		if (!selectedUserId) return;
		try {
			overview = await getMemoryOverview(selectedUserId);
		} catch {
			overview = null;
		}
	}

	$effect(() => {
		void selectedUserId;
		void refreshKey;
		void loadOverview();
	});

	function onUserSelect(event: Event) {
		const target = event.currentTarget as HTMLSelectElement;
		selectedUserId = target.value;
		selectedNote = null;
		entityFilter = null;
		refreshKey += 1;
	}

	function openNote(note: NoteView) {
		selectedNote = note;
	}

	function handleNoteChanged(updated: NoteView | null) {
		selectedNote = updated;
		refreshKey += 1;
	}

	function pickEntity(entity: EntityView) {
		entityFilter = entity;
		activeTab = 'notes';
	}

	onMount(async () => {
		if (isAdmin) {
			try {
				adminUsers = await listUsers();
			} catch {
				adminUsers = [];
			}
		}
	});
</script>

<div class="page max-w-6xl space-y-4">
	<header class="flex flex-wrap items-start justify-between gap-4">
		<div>
			<h1 class="page-title">Память</h1>
			<p class="page-sub">
				Долгосрочная память ассистента: заметки, постоянный контекст, сущности и
				история операций.
			</p>
		</div>
		<div class="flex flex-wrap items-center gap-3">
			{#if overview}
				<div class="flex items-center gap-1.5 text-xs">
					<span
						class="inline-flex items-center gap-1.5 rounded-md border border-gray-800 bg-gray-900/60 px-2 py-1 text-gray-300"
						title="Активных заметок"
					>
						📝 <span class="font-semibold text-gray-100">{activeNotesTotal}</span>
					</span>
					<span
						class="inline-flex items-center gap-1.5 rounded-md border border-gray-800 bg-gray-900/60 px-2 py-1 text-gray-300"
						title="В профиле"
					>
						📌 <span class="font-semibold text-gray-100">{overview.pinned_count}</span>
					</span>
					<span
						class="inline-flex items-center gap-1.5 rounded-md border border-gray-800 bg-gray-900/60 px-2 py-1 text-gray-300"
						title="В журнале"
					>
						📓 <span class="font-semibold text-gray-100">{overview.journal_count}</span>
					</span>
					<span
						class="inline-flex items-center gap-1.5 rounded-md border border-gray-800 bg-gray-900/60 px-2 py-1 text-gray-300"
						title="Сущностей"
					>
						@ <span class="font-semibold text-gray-100">{overview.entities_count}</span>
					</span>
				</div>
			{/if}
			{#if isAdmin && adminUsers.length > 0}
				<label class="flex items-center gap-2 text-sm">
					<span class="text-gray-400">Пользователь:</span>
					<select class="input input-sm w-auto" value={selectedUserId} onchange={onUserSelect}>
						{#each adminUsers as u (u.user_id)}
							<option value={u.user_id}>
								{u.login ?? u.user_id.slice(0, 8)} · {u.role}
							</option>
						{/each}
					</select>
				</label>
			{/if}
		</div>
	</header>

	<nav class="flex items-center gap-1 border-b border-gray-800">
		{#each TABS as tab (tab.id)}
			<button
				type="button"
				onclick={() => (activeTab = tab.id)}
				class="-mb-px rounded-t-md border-b-2 px-4 py-2 text-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-indigo-500/40 {activeTab ===
				tab.id
					? 'border-indigo-500 text-white'
					: 'border-transparent text-gray-400 hover:bg-white/5 hover:text-white'}"
			>
				{tab.label}
				{#if tab.id === 'entities' && overview}
					<span class="text-gray-500">({overview.entities_count})</span>
				{/if}
			</button>
		{/each}
	</nav>

	{#if selectedUserId}
		{#if activeTab === 'notes'}
			<NotesSection
				userId={selectedUserId}
				{entityFilter}
				onClearEntity={() => (entityFilter = null)}
				onOpen={openNote}
				onMutated={() => (refreshKey += 1)}
				{refreshKey}
			/>
		{:else if activeTab === 'context'}
			<ContextSection userId={selectedUserId} onOpen={openNote} {refreshKey} />
		{:else if activeTab === 'entities'}
			<EntitiesSection userId={selectedUserId} onPickEntity={pickEntity} {refreshKey} />
		{:else}
			<OpsSection userId={selectedUserId} {refreshKey} />
		{/if}
	{/if}
</div>

{#if selectedNote && selectedUserId}
	<NoteDetails
		userId={selectedUserId}
		note={selectedNote}
		onClose={() => (selectedNote = null)}
		onChanged={handleNoteChanged}
	/>
{/if}
