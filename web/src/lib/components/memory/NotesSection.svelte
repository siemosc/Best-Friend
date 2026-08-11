<script lang="ts">
	// Секция «Заметки»: фильтры-чипы, два режима поиска (фильтр по подстроке /
	// recall «как видит модель»), пагинация, создание заметки.
	import { ApiError, listMemoryNotes, searchMemoryNotes } from '$lib/api';
	import type {
		EntityView,
		NoteKind,
		NoteStatus,
		NoteSubject,
		NoteView,
	} from '$lib/types';
	import NoteCreateForm from './NoteCreateForm.svelte';
	import NoteListItem from './NoteListItem.svelte';
	import { KIND_LABELS, STATUS_LABELS, SUBJECT_LABELS } from './labels';

	const {
		userId,
		entityFilter,
		onClearEntity,
		onOpen,
		onMutated,
		refreshKey,
	}: {
		userId: string;
		entityFilter: EntityView | null;
		onClearEntity: () => void;
		onOpen: (note: NoteView) => void;
		// Любая мутация уходит наверх — родитель инкрементит refreshKey,
		// обновляя и overview-чипы, и остальные секции.
		onMutated: () => void;
		refreshKey: number;
	} = $props();

	const ALL_KINDS = Object.keys(KIND_LABELS) as NoteKind[];
	const ALL_SUBJECTS = Object.keys(SUBJECT_LABELS) as NoteSubject[];
	const ALL_STATUSES = Object.keys(STATUS_LABELS) as NoteStatus[];
	const PAGE_LIMIT = 25;

	let kinds: NoteKind[] = $state([]);
	let subjects: NoteSubject[] = $state([]);
	let statuses: NoteStatus[] = $state(['active']);
	let searchText = $state('');
	// generic-форма: сравнения режима в top-level $derived требуют полного union-типа.
	let searchMode = $state<'filter' | 'recall'>('filter');
	let offset = $state(0);
	// q, под которым загружена текущая страница (фильтр-режим).
	let appliedQuery = $state('');

	let items: NoteView[] = $state([]);
	let total = $state(0);
	// Чем загружены items сейчас: тумблер searchMode — лишь намерение,
	// recall-вид включается только после успешного recall-запроса.
	let resultMode = $state<'list' | 'recall'>('list');
	let recallEmpty = $state(false);
	let loading = $state(true);
	let error: string | null = $state(null);
	// Монотонный id запроса: коммитит состояние только последний выстреливший
	// запрос — перекрытия listing/recall не перезаписывают друг друга.
	let requestSeq = 0;

	function toggle<T>(list: T[], value: T): T[] {
		return list.includes(value)
			? list.filter((item) => item !== value)
			: [...list, value];
	}

	async function loadPage() {
		const requestId = ++requestSeq;
		loading = true;
		error = null;
		try {
			const page = await listMemoryNotes(userId, {
				kinds: kinds.length ? kinds : undefined,
				subjects: subjects.length ? subjects : undefined,
				statuses: statuses.length ? statuses : undefined,
				entity_id: entityFilter?.id,
				q: appliedQuery || undefined,
				limit: PAGE_LIMIT,
				offset,
			});
			if (requestId !== requestSeq) return; // ответ устарел
			items = page.items;
			total = page.total;
			resultMode = 'list';
			recallEmpty = false;
		} catch (e) {
			if (requestId !== requestSeq) return;
			error = e instanceof ApiError ? e.message : 'Не удалось загрузить заметки';
		} finally {
			if (requestId === requestSeq) loading = false;
		}
	}

	async function runRecall() {
		const requestId = ++requestSeq;
		loading = true;
		error = null;
		try {
			const result = await searchMemoryNotes(userId, searchText.trim(), {
				kinds: kinds.length ? kinds : undefined,
				subjects: subjects.length ? subjects : undefined,
			});
			if (requestId !== requestSeq) return; // ответ устарел
			items = result.items;
			total = result.items.length;
			recallEmpty = !result.gate_passed;
			resultMode = 'recall';
		} catch (e) {
			if (requestId !== requestSeq) return;
			error = e instanceof ApiError ? e.message : 'Поиск не удался';
		} finally {
			if (requestId === requestSeq) loading = false;
		}
	}

	function submitSearch(event: SubmitEvent) {
		event.preventDefault();
		offset = 0;
		if (searchMode === 'recall') {
			if (searchText.trim() === '') return;
			void runRecall();
		} else {
			appliedQuery = searchText.trim();
			void loadPage();
		}
	}

	function resetSearch() {
		searchText = '';
		appliedQuery = '';
		offset = 0;
		void loadPage();
	}

	function goToPage(nextOffset: number) {
		offset = nextOffset;
		void loadPage();
	}

	// Перезагрузка листинга при смене контекста: юзер, фильтры, refreshKey.
	// offset и appliedQuery намеренно вне зависимостей — страничные переходы
	// и сабмиты зовут loadPage/runRecall императивно, не плодя параллельный
	// запрос из эффекта.
	$effect(() => {
		void userId;
		void refreshKey;
		void kinds;
		void subjects;
		void statuses;
		void entityFilter;
		void loadPage();
	});

	const pageStart = $derived(total === 0 ? 0 : offset + 1);
	const pageEnd = $derived(Math.min(offset + PAGE_LIMIT, total));
	const isRecallView = $derived(resultMode === 'recall' && !loading && !error);
</script>

<div class="space-y-3">
	<form onsubmit={submitSearch} class="flex flex-wrap items-center gap-2">
		<div class="flex overflow-hidden rounded-lg border border-gray-700 text-xs">
			<button
				type="button"
				onclick={() => (searchMode = 'filter')}
				class="px-2.5 py-1.5 transition-colors {searchMode === 'filter'
					? 'bg-gray-700 text-white'
					: 'bg-gray-900 text-gray-400 hover:text-white'}"
				title="Фильтр по подстроке контента"
			>
				Фильтр
			</button>
			<button
				type="button"
				onclick={() => (searchMode = 'recall')}
				class="px-2.5 py-1.5 transition-colors {searchMode === 'recall'
					? 'bg-indigo-600 text-white'
					: 'bg-gray-900 text-gray-400 hover:text-white'}"
				title="Гибридный recall — то, что увидела бы модель"
			>
				Как модель
			</button>
		</div>
		<input
			type="text"
			bind:value={searchText}
			placeholder={searchMode === 'recall'
				? 'Запрос к памяти — как сказал бы в диалоге…'
				: 'Подстрока в тексте заметки…'}
			class="input input-sm min-w-48 flex-1"
		/>
		<button type="submit" class="btn-primary btn-sm">Найти</button>
		{#if appliedQuery || searchText}
			<button type="button" onclick={resetSearch} class="btn-ghost btn-sm">Сброс</button>
		{/if}
	</form>

	<div class="flex flex-wrap items-center gap-1.5 text-xs">
		{#each ALL_KINDS as kind (kind)}
			<button
				type="button"
				onclick={() => {
					offset = 0;
					kinds = toggle(kinds, kind);
				}}
				class="chip {kinds.includes(kind)
					? 'border-blue-500 bg-blue-600/30 text-blue-200'
					: 'chip-off'}"
			>
				{KIND_LABELS[kind]}
			</button>
		{/each}
		<span class="text-gray-700">|</span>
		{#each ALL_SUBJECTS as subject (subject)}
			<button
				type="button"
				onclick={() => {
					offset = 0;
					subjects = toggle(subjects, subject);
				}}
				class="chip {subjects.includes(subject)
					? 'border-emerald-500 bg-emerald-600/30 text-emerald-200'
					: 'chip-off'}"
			>
				о: {SUBJECT_LABELS[subject]}
			</button>
		{/each}
		<span class="text-gray-700">|</span>
		{#each ALL_STATUSES as status (status)}
			<button
				type="button"
				onclick={() => {
					offset = 0;
					statuses = toggle(statuses, status);
				}}
				class="chip {statuses.includes(status)
					? 'border-gray-500 bg-gray-600/40 text-gray-200'
					: 'chip-off'}"
			>
				{STATUS_LABELS[status]}
			</button>
		{/each}
	</div>

	{#if entityFilter}
		<div class="flex items-center gap-2 text-sm">
			<span class="text-gray-400">Сущность:</span>
			<span class="rounded bg-gray-800 px-2 py-0.5 font-mono text-gray-200">
				@{entityFilter.canonical_name}
			</span>
			<button
				type="button"
				onclick={onClearEntity}
				class="text-gray-500 transition-colors hover:text-white"
				title="Снять фильтр по сущности"
			>
				✕
			</button>
		</div>
	{/if}

	<NoteCreateForm
		{userId}
		onCreated={() => {
			offset = 0;
			onMutated();
		}}
	/>

	{#if error}
		<div class="alert-error">{error}</div>
	{/if}

	{#if loading}
		<p class="empty-state">Загрузка…</p>
	{:else if isRecallView && recallEmpty}
		<p class="empty-state">
			Recall пуст — гейт не нашёл уверенного ответа. Модель в этом месте не получила
			бы ничего.
		</p>
	{:else if items.length === 0}
		<p class="empty-state">Заметок не найдено.</p>
	{:else}
		{#if isRecallView}
			<p class="text-xs text-indigo-300">
				Выдача recall ({items.length}) — ровно то, что увидела бы модель по этому
				запросу.
			</p>
		{/if}
		<ul class="space-y-1.5">
			{#each items as note (note.id)}
				<li><NoteListItem {note} {onOpen} /></li>
			{/each}
		</ul>
		{#if !isRecallView && total > PAGE_LIMIT}
			<div class="flex items-center justify-between pt-1 text-sm text-gray-400">
				<button
					type="button"
					disabled={offset === 0}
					onclick={() => goToPage(Math.max(0, offset - PAGE_LIMIT))}
					class="btn-secondary btn-sm"
				>
					← Назад
				</button>
				<span>{pageStart}–{pageEnd} из {total}</span>
				<button
					type="button"
					disabled={pageEnd >= total}
					onclick={() => goToPage(offset + PAGE_LIMIT)}
					class="btn-secondary btn-sm"
				>
					Вперёд →
				</button>
			</div>
		{/if}
	{/if}
</div>
