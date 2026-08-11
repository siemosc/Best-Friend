<script lang="ts">
	import { onMount } from 'svelte';
	import {
		ApiError,
		getAssistantConfig,
		listUsers,
		resetAssistantConfig,
		updateAssistantConfig,
	} from '$lib/api';
	import { user as currentUser } from '$lib/stores/user';
	import type { AssistantConfigResponse, UserResponse } from '$lib/types';

	const isAdmin = $derived($currentUser?.role === 'admin');

	let selectedUserId = $state($currentUser?.user_id ?? '');
	let adminUsers: UserResponse[] = $state([]);
	let config: AssistantConfigResponse | null = $state(null);
	let userInstruction = $state('');
	let llmConfigText = $state('{}');
	let loading = $state(true);
	let saving = $state(false);
	let resetting = $state(false);
	let error: string | null = $state(null);
	let success: string | null = $state(null);

	function seedForm(cfg: AssistantConfigResponse): void {
		userInstruction = cfg.user_instruction;
		llmConfigText = JSON.stringify(cfg.llm_custom_config ?? {}, null, 2);
	}

	async function loadConfig() {
		if (!selectedUserId) return;
		loading = true;
		error = null;
		success = null;
		try {
			config = await getAssistantConfig(selectedUserId);
			seedForm(config);
		} catch (e) {
			error = e instanceof ApiError ? e.message : 'Не удалось загрузить';
		} finally {
			loading = false;
		}
	}

	async function onUserSelect(event: Event) {
		const target = event.currentTarget as HTMLSelectElement;
		selectedUserId = target.value;
		await loadConfig();
	}

	/** Парсит JSON llm_custom_config; null + выставленный error при невалидном вводе. */
	function parseLlmConfig(): Record<string, unknown> | null {
		const text = llmConfigText.trim();
		if (text === '') return {};
		let parsed: unknown;
		try {
			parsed = JSON.parse(text);
		} catch {
			error = 'llm_custom_config — невалидный JSON';
			return null;
		}
		if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
			error = 'llm_custom_config должен быть JSON-объектом';
			return null;
		}
		return parsed as Record<string, unknown>;
	}

	async function handleSave(event: SubmitEvent) {
		event.preventDefault();
		error = null;
		success = null;
		const llmConfig = parseLlmConfig();
		if (llmConfig === null) return; // ошибка парсинга уже выставлена — не отправляем
		saving = true;
		try {
			config = await updateAssistantConfig(selectedUserId, {
				user_instruction: userInstruction,
				llm_custom_config: llmConfig,
			});
			seedForm(config);
			success = 'Сохранено';
		} catch (e) {
			if (e instanceof ApiError || e instanceof Error) {
				error = e.message;
			} else {
				error = 'Не удалось сохранить';
			}
		} finally {
			saving = false;
		}
	}

	async function handleReset() {
		if (
			!confirm('Сбросить конфиг к system defaults? Текущие значения будут потеряны.')
		) {
			return;
		}
		error = null;
		success = null;
		resetting = true;
		try {
			config = await resetAssistantConfig(selectedUserId);
			seedForm(config);
			success = 'Конфиг сброшен к defaults';
		} catch (e) {
			error = e instanceof ApiError ? e.message : 'Не удалось сбросить';
		} finally {
			resetting = false;
		}
	}

	onMount(async () => {
		if (isAdmin) {
			try {
				adminUsers = await listUsers();
			} catch {
				adminUsers = [];
			}
		}
		await loadConfig();
	});
</script>

<div class="page max-w-3xl space-y-4">
	<header class="flex flex-wrap items-start justify-between gap-4">
		<div>
			<h1 class="page-title">Ассистент</h1>
			<p class="page-sub">Инструкция и кастомный конфиг модели для всего графа.</p>
		</div>
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
	</header>

	{#if error}
		<div class="alert-error">{error}</div>
	{/if}
	{#if success}
		<div class="alert-success">✓ {success}</div>
	{/if}

	{#if loading}
		<div class="flex items-center gap-2 text-gray-400">
			<span class="spinner h-4 w-4"></span> Загрузка…
		</div>
	{:else if config}
		<form onsubmit={handleSave} class="space-y-4">
			<section class="card space-y-2">
				<div>
					<h2 class="text-base font-medium text-gray-100">User instruction</h2>
					<p class="section-sub">Системная инструкция, применяется ко всему графу.</p>
				</div>
				<textarea
					bind:value={userInstruction}
					class="input font-mono"
					rows="4"
					maxlength="8000"
					placeholder="Например: отвечай кратко и по делу…"
				></textarea>
			</section>

			<section class="card space-y-2">
				<div>
					<h2 class="text-base font-medium text-gray-100">llm_custom_config (JSON)</h2>
					<p class="section-sub">
						Полный конфиг модели (как в таблице models): provider, model, api_base,
						temperature и т.д. Пусто или <code class="rounded bg-gray-800 px-1 py-0.5 text-gray-300"
							>{'{}'}</code
						> → системная модель по умолчанию.
					</p>
				</div>
				<textarea
					bind:value={llmConfigText}
					class="input font-mono leading-relaxed"
					rows="12"
					spellcheck="false"
					placeholder={'{\n  "provider": "openrouter",\n  "model": "deepseek/deepseek-v4-flash"\n}'}
				></textarea>
			</section>

			<div class="flex flex-wrap items-center justify-between gap-3">
				<button
					type="button"
					onclick={handleReset}
					disabled={resetting || saving}
					class="btn-secondary"
				>
					{resetting ? 'Сбрасываем…' : 'Сбросить к system defaults'}
				</button>
				<button type="submit" disabled={saving || resetting} class="btn-primary px-6">
					{saving ? 'Сохраняем…' : 'Сохранить'}
				</button>
			</div>
		</form>
	{/if}
</div>
