<script lang="ts">
	// Панель заметки: полный контент, мета, действия (pin/журнал/субъект/правка/
	// удаление), история операций, сцена-источник из сырого лога.
	// Мутации разрешены только active-заметкам; удаление — любому статусу.
	import {
		ApiError,
		deleteMemoryNote,
		getMemoryNoteOps,
		getMemoryTurns,
		reviseMemoryNote,
		updateMemoryNote,
	} from '$lib/api';
	import type {
		MemoryOpView,
		NoteSubject,
		NoteView,
		PinSection,
		TurnView,
	} from '$lib/types';
	import NoteBadges from './NoteBadges.svelte';
	import {
		formatDateTime,
		OP_LABELS,
		PIN_SECTION_LABELS,
		PIPELINE_BADGE,
		PIPELINE_LABELS,
		SUBJECT_LABELS,
	} from './labels';

	const {
		userId,
		note,
		onClose,
		onChanged,
	}: {
		userId: string;
		note: NoteView;
		onClose: () => void;
		// null — заметка удалена (закрыть панель), иначе — свежее состояние.
		onChanged: (note: NoteView | null) => void;
	} = $props();

	const PIN_SECTIONS: PinSection[] = [
		'identity',
		'preferences',
		'relationships',
		'rules',
	];
	const SUBJECTS: NoteSubject[] = ['user', 'agent', 'world'];

	const isActive = $derived(note.status === 'active');
	const subjectEditable = $derived(
		isActive && (note.kind === 'fact' || note.kind === 'observation'),
	);

	let editing = $state(false);
	let editText = $state('');
	let pinSection: PinSection = $state('preferences');
	let busy = $state(false);
	let error: string | null = $state(null);
	let ops: MemoryOpView[] = $state([]);
	let opsLoading = $state(false);
	let turns: TurnView[] | null = $state(null);
	let turnsLoading = $state(false);

	// Смена выбранной заметки сбрасывает локальное состояние и подтягивает ops.
	$effect(() => {
		void note.id;
		editing = false;
		error = null;
		turns = null;
		void loadOps();
	});

	async function loadOps() {
		opsLoading = true;
		try {
			ops = await getMemoryNoteOps(userId, note.id);
		} catch {
			ops = [];
		} finally {
			opsLoading = false;
		}
	}

	async function run(action: () => Promise<NoteView | null>) {
		busy = true;
		error = null;
		try {
			const updated = await action();
			onChanged(updated);
			if (updated !== null) await loadOps();
		} catch (e) {
			error = e instanceof ApiError ? e.message : 'Операция не удалась';
		} finally {
			busy = false;
		}
	}

	const togglePin = () =>
		run(() =>
			note.pinned
				? updateMemoryNote(userId, note.id, { pinned: false })
				: updateMemoryNote(userId, note.id, {
						pinned: true,
						pin_section: pinSection,
					}),
		);

	const toggleJournal = () =>
		run(() => updateMemoryNote(userId, note.id, { in_journal: !note.in_journal }));

	const changeSubject = (event: Event) => {
		const value = (event.currentTarget as HTMLSelectElement).value as NoteSubject;
		if (value === note.subject) return;
		void run(() => updateMemoryNote(userId, note.id, { subject: value }));
	};

	function startEdit() {
		editText = note.content;
		editing = true;
	}

	const saveEdit = () =>
		run(async () => {
			const revised = await reviseMemoryNote(userId, note.id, editText.trim());
			editing = false;
			return revised;
		});

	const removeNote = () => {
		if (!confirm('Удалить заметку из памяти навсегда? Действие необратимо.')) return;
		void run(async () => {
			await deleteMemoryNote(userId, note.id);
			return null;
		});
	};

	async function loadTurns() {
		if (note.source_turn_start === null || note.source_turn_end === null) return;
		turnsLoading = true;
		error = null;
		try {
			const response = await getMemoryTurns(
				userId,
				note.source_turn_start,
				note.source_turn_end,
			);
			turns = response.items;
		} catch (e) {
			error = e instanceof ApiError ? e.message : 'Не удалось загрузить лог';
		} finally {
			turnsLoading = false;
		}
	}
</script>

<!-- backdrop: клик вне панели закрывает -->
<button
	type="button"
	onclick={onClose}
	aria-label="Закрыть панель"
	class="fixed inset-0 z-30 cursor-default bg-black/50 backdrop-blur-sm"
></button>

<aside
	class="fixed inset-y-0 right-0 z-40 flex w-full animate-slide-in-right flex-col border-l border-gray-800 bg-gray-950 shadow-2xl sm:w-[30rem]"
>
	<header class="flex items-start gap-2 border-b border-gray-800 px-4 py-3">
		<div class="flex min-w-0 flex-1 flex-wrap items-center gap-1.5">
			<NoteBadges {note} />
		</div>
		<button
			type="button"
			onclick={onClose}
			class="shrink-0 rounded-md px-2 py-1 text-gray-400 transition-colors hover:bg-gray-800 hover:text-white"
			title="Закрыть"
		>
			✕
		</button>
	</header>

	<div class="flex-1 space-y-4 overflow-y-auto px-4 py-3">
		{#if error}
			<div class="alert-error">{error}</div>
		{/if}

		{#if editing}
			<div class="space-y-2">
				<textarea bind:value={editText} rows="6" class="input"></textarea>
				<p class="text-xs text-gray-500">
					Правка создаст новую версию; прежняя останется в истории как «заменена».
				</p>
				<div class="flex gap-2">
					<button
						type="button"
						onclick={saveEdit}
						disabled={busy || editText.trim() === ''}
						class="btn-primary btn-sm"
					>
						{busy ? 'Сохраняем…' : 'Сохранить'}
					</button>
					<button
						type="button"
						onclick={() => (editing = false)}
						disabled={busy}
						class="btn-secondary btn-sm"
					>
						Отмена
					</button>
				</div>
			</div>
		{:else}
			<p class="whitespace-pre-wrap text-sm leading-relaxed">{note.content}</p>
		{/if}

		{#if note.entities.length > 0}
			<div class="flex flex-wrap items-center gap-1.5">
				{#each note.entities as entity (entity.id)}
					<span class="rounded bg-gray-800/80 px-2 py-0.5 font-mono text-xs text-gray-300">
						@{entity.name}
					</span>
				{/each}
			</div>
		{/if}

		<dl class="grid grid-cols-2 gap-x-4 gap-y-1 text-xs text-gray-400">
			<dt>Записано</dt>
			<dd class="text-gray-300">{formatDateTime(note.observed_at)}</dd>
			{#if note.event_time}
				<dt>Время события</dt>
				<dd class="text-gray-300">{formatDateTime(note.event_time)}</dd>
			{/if}
			{#if note.source_turn_start !== null && note.source_turn_end !== null}
				<dt>Источник</dt>
				<dd class="text-gray-300">ходы {note.source_turn_start}–{note.source_turn_end}</dd>
			{/if}
			<dt>Использована в recall</dt>
			<dd class="text-gray-300">{note.use_count} раз</dd>
		</dl>

		{#if isActive}
			<section class="space-y-2 border-t border-gray-800 pt-3">
				<h3 class="text-xs uppercase tracking-wider text-gray-500">Действия</h3>
				<div class="flex flex-wrap items-center gap-2">
					{#if note.pinned}
						<button type="button" onclick={togglePin} disabled={busy} class="btn-secondary btn-sm">
							Открепить из профиля
						</button>
					{:else}
						<select bind:value={pinSection} class="input input-sm w-auto">
							{#each PIN_SECTIONS as section (section)}
								<option value={section}>{PIN_SECTION_LABELS[section]}</option>
							{/each}
						</select>
						<button type="button" onclick={togglePin} disabled={busy} class="btn-secondary btn-sm">
							📌 Закрепить
						</button>
					{/if}
					<button type="button" onclick={toggleJournal} disabled={busy} class="btn-secondary btn-sm">
						{note.in_journal ? 'Убрать из журнала' : '📓 В журнал'}
					</button>
					<button
						type="button"
						onclick={startEdit}
						disabled={busy || editing}
						class="btn-secondary btn-sm"
					>
						✏️ Править
					</button>
				</div>
				{#if subjectEditable}
					<label class="flex items-center gap-2 text-sm text-gray-400">
						<span>Субъект:</span>
						<select
							value={note.subject ?? ''}
							onchange={changeSubject}
							disabled={busy}
							class="input input-sm w-auto"
						>
							{#if note.subject === null}
								<option value="">— не определён —</option>
							{/if}
							{#each SUBJECTS as subject (subject)}
								<option value={subject}>{SUBJECT_LABELS[subject]}</option>
							{/each}
						</select>
					</label>
				{/if}
			</section>
		{/if}

		<section class="border-t border-gray-800 pt-3">
			<button type="button" onclick={removeNote} disabled={busy} class="btn-danger btn-sm">
				🗑 Удалить навсегда
			</button>
		</section>

		{#if note.source_turn_start !== null && note.source_turn_end !== null}
			<section class="space-y-2 border-t border-gray-800 pt-3">
				<div class="flex items-center justify-between">
					<h3 class="text-xs uppercase tracking-wider text-gray-500">Сцена-источник</h3>
					{#if turns === null}
						<button
							type="button"
							onclick={loadTurns}
							disabled={turnsLoading}
							class="btn-secondary btn-sm"
						>
							{turnsLoading ? 'Загрузка…' : 'Показать диалог'}
						</button>
					{/if}
				</div>
				{#if turns !== null}
					{#each turns as turn (turn.id)}
						<div class="rounded-lg border border-gray-800 bg-gray-900/40 p-2.5">
							<p class="mb-1 text-[11px] text-gray-500">
								Ход {turn.id} · {formatDateTime(turn.created_at)}
							</p>
							<pre
								class="whitespace-pre-wrap font-sans text-xs leading-relaxed text-gray-300">{turn.rendered}</pre>
						</div>
					{/each}
					{#if turns.length === 0}
						<p class="text-xs text-gray-500">Ходы не найдены (лог мог быть очищен).</p>
					{/if}
				{/if}
			</section>
		{/if}

		<section class="space-y-2 border-t border-gray-800 pt-3">
			<h3 class="text-xs uppercase tracking-wider text-gray-500">История операций</h3>
			{#if opsLoading}
				<p class="text-xs text-gray-500">Загрузка…</p>
			{:else if ops.length === 0}
				<p class="text-xs text-gray-500">Операций не зафиксировано.</p>
			{:else}
				<ul class="space-y-1.5">
					{#each ops as op (op.id)}
						<li class="flex flex-wrap items-baseline gap-2 text-xs text-gray-400">
							<span class="whitespace-nowrap text-gray-500">
								{formatDateTime(op.created_at)}
							</span>
							<span class="badge {PIPELINE_BADGE[op.pipeline]}">
								{PIPELINE_LABELS[op.pipeline]}
							</span>
							<span class="text-gray-300">{OP_LABELS[op.op] ?? op.op}</span>
							{#if op.detail}
								<span class="text-gray-500">· {op.detail}</span>
							{/if}
						</li>
					{/each}
				</ul>
			{/if}
		</section>
	</div>
</aside>
