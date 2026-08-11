<script lang="ts">
	// Создание заметки руками: факт / предпочтение / правило (+ pin в профиль).
	// Субъект у preference/rule прибит инвариантом — селект блокируется.
	import { ApiError, createMemoryNote } from '$lib/api';
	import type { NoteSubject, PinSection } from '$lib/types';
	import { PIN_SECTION_LABELS, SUBJECT_LABELS } from './labels';

	const {
		userId,
		onCreated,
	}: { userId: string; onCreated: () => void } = $props();

	type WritableKind = 'fact' | 'preference' | 'rule';

	const KIND_OPTIONS: { value: WritableKind; label: string }[] = [
		{ value: 'fact', label: 'факт' },
		{ value: 'preference', label: 'предпочтение' },
		{ value: 'rule', label: 'правило' },
	];
	const SUBJECTS: NoteSubject[] = ['user', 'agent', 'world'];
	const PIN_SECTIONS: PinSection[] = [
		'identity',
		'preferences',
		'relationships',
		'rules',
	];

	let open = $state(false);
	// generic-форма $state: иначе TS сужает тип к литералу инициализатора
	// и сравнения в top-level $derived перестают компилироваться.
	let kind = $state<WritableKind>('fact');
	let subject = $state<NoteSubject>('user');
	let content = $state('');
	let pin = $state(false);
	let pinSection: PinSection = $state('preferences');
	let busy = $state(false);
	let error: string | null = $state(null);

	// Инвариант субъекта: предпочтение — всегда о пользователе, правило — об агенте.
	const fixedSubject: NoteSubject | null = $derived(
		kind === 'preference' ? 'user' : kind === 'rule' ? 'agent' : null,
	);
	const effectiveSubject = $derived(fixedSubject ?? subject);

	async function submit(event: SubmitEvent) {
		event.preventDefault();
		busy = true;
		error = null;
		try {
			await createMemoryNote(userId, {
				kind,
				subject: effectiveSubject,
				content: content.trim(),
				pin,
				pin_section: pin ? pinSection : null,
			});
			content = '';
			pin = false;
			open = false;
			onCreated();
		} catch (e) {
			error = e instanceof ApiError ? e.message : 'Не удалось сохранить';
		} finally {
			busy = false;
		}
	}
</script>

{#if !open}
	<button type="button" onclick={() => (open = true)} class="btn-secondary btn-sm">
		+ Заметка
	</button>
{:else}
	<form onsubmit={submit} class="surface w-full space-y-3 p-4">
		{#if error}
			<div class="alert-error">{error}</div>
		{/if}
		<textarea
			bind:value={content}
			rows="3"
			placeholder="Самодостаточная формулировка с конкретными именами и значениями…"
			class="input"
		></textarea>
		<div class="flex flex-wrap items-center gap-3 text-sm">
			<select bind:value={kind} class="input input-sm w-auto">
				{#each KIND_OPTIONS as option (option.value)}
					<option value={option.value}>{option.label}</option>
				{/each}
			</select>
			{#if fixedSubject !== null}
				<span class="text-gray-500" title="Субъект задан видом заметки">
					субъект: {SUBJECT_LABELS[fixedSubject]}
				</span>
			{:else}
				<select bind:value={subject} class="input input-sm w-auto">
					{#each SUBJECTS as option (option)}
						<option value={option}>о: {SUBJECT_LABELS[option]}</option>
					{/each}
				</select>
			{/if}
			<label class="flex items-center gap-1.5 text-gray-300">
				<input type="checkbox" bind:checked={pin} class="accent-indigo-600" />
				📌 в профиль
			</label>
			{#if pin}
				<select bind:value={pinSection} class="input input-sm w-auto">
					{#each PIN_SECTIONS as section (section)}
						<option value={section}>{PIN_SECTION_LABELS[section]}</option>
					{/each}
				</select>
			{/if}
			<div class="ml-auto flex gap-2">
				<button
					type="button"
					onclick={() => (open = false)}
					disabled={busy}
					class="btn-secondary btn-sm"
				>
					Отмена
				</button>
				<button
					type="submit"
					disabled={busy || content.trim() === ''}
					class="btn-primary btn-sm"
				>
					{busy ? 'Сохраняем…' : 'Сохранить'}
				</button>
			</div>
		</div>
	</form>
{/if}
