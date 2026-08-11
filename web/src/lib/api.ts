// HTTP-клиент control_plane. В dev все запросы идут через Vite proxy на /api/*.
// Credentials: 'include' — браузер сам передаёт и принимает cookie `bestfiend_session`.

import type {
	AdminUpdateUserRequest,
	ApiErrorPayload,
	AssistantConfigResponse,
	ChangePasswordRequest,
	CreateMcpConnectionRequest,
	CreateNoteRequest,
	DashboardHealthSnapshot,
	DiscoverPreviewRequest,
	DiscoverPreviewResponse,
	EntityView,
	McpConnectionView,
	McpOAuthStartResponse,
	McpServerSubscriptionView,
	MemoryContextResponse,
	MemoryOpView,
	MemoryOverviewResponse,
	MemoryPipeline,
	NoteKind,
	NoteSearchResponse,
	NoteSubject,
	NotesListParams,
	NotesPageResponse,
	NoteView,
	OpsPageResponse,
	TurnsRangeResponse,
	UpdateAssistantConfigRequest,
	UpdateMcpConnectionRequest,
	UpdateNoteRequest,
	UpdateProfileRequest,
	UpsertSubscriptionRequest,
	UserResponse,
} from './types';

export class ApiError extends Error {
	constructor(
		public readonly status: number,
		public readonly errorCode: string,
		message: string,
	) {
		super(message);
	}
}

type FastApiValidationItem = {
	loc?: unknown;
	msg?: unknown;
	type?: unknown;
};

/**
 * Нормализует `detail` в читаемую строку.
 *
 * FastAPI отдаёт 422 как `{detail: [{loc, msg, type}, ...]}` — массив объектов.
 * Domain-ошибки control_plane отдают `{detail: "..."}` — строку.
 */
export function formatErrorDetail(
	detail: unknown,
	fallback: string,
): string {
	if (typeof detail === 'string' && detail.trim() !== '') return detail;
	if (Array.isArray(detail)) {
		const lines = detail
			.map((item) => formatValidationItem(item))
			.filter((s): s is string => s.length > 0);
		if (lines.length > 0) return lines.join('; ');
	}
	if (detail && typeof detail === 'object') {
		const item = formatValidationItem(detail);
		if (item) return item;
	}
	return fallback;
}

function formatValidationItem(item: unknown): string {
	if (typeof item === 'string') return item;
	if (!item || typeof item !== 'object') return '';
	const obj = item as FastApiValidationItem;
	const msg = typeof obj.msg === 'string' ? obj.msg : '';
	const loc = Array.isArray(obj.loc)
		? obj.loc
				.filter((p) => p !== 'body')
				.map((p) => String(p))
				.join('.')
		: '';
	if (loc && msg) return `${loc}: ${msg}`;
	return msg || loc;
}

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
	const res = await fetch(`/api${path}`, {
		...init,
		credentials: 'include',
		headers: {
			'Content-Type': 'application/json',
			...(init?.headers ?? {}),
		},
	});
	if (!res.ok) {
		const body = (await res.json().catch(() => ({}))) as Partial<
			ApiErrorPayload & { detail: unknown }
		>;
		throw new ApiError(
			res.status,
			body.error_code ?? 'UNKNOWN',
			formatErrorDetail(body.detail, res.statusText),
		);
	}
	if (res.status === 204) return undefined as T;
	return (await res.json()) as T;
}

export const authMe = () => apiFetch<UserResponse>('/auth/me');

export const authLogin = (login: string, password: string) =>
	apiFetch<UserResponse>('/auth/login', {
		method: 'POST',
		body: JSON.stringify({ login, password }),
	});

export const authBind = (code: string, login: string, password: string) =>
	apiFetch<UserResponse>('/auth/bind', {
		method: 'POST',
		body: JSON.stringify({ code, login, password }),
	});

export const authLogout = () =>
	apiFetch<void>('/auth/logout', { method: 'POST' });

export const listUsers = () => apiFetch<UserResponse[]>('/users');

export const adminUpdateUser = (
	userId: string,
	patch: AdminUpdateUserRequest,
) =>
	apiFetch<UserResponse>(`/users/${userId}`, {
		method: 'PATCH',
		body: JSON.stringify(patch),
	});

export const updateProfile = (patch: UpdateProfileRequest) =>
	apiFetch<UserResponse>('/users/me', {
		method: 'PATCH',
		body: JSON.stringify(patch),
	});

export const changePassword = (payload: ChangePasswordRequest) =>
	apiFetch<void>('/auth/change-password', {
		method: 'POST',
		body: JSON.stringify(payload),
	});

// ── Assistant config ─────────────────────────────────────────
export const getAssistantConfig = (userId: string) =>
	apiFetch<AssistantConfigResponse>(`/users/${userId}/assistant-config`);

export const updateAssistantConfig = (
	userId: string,
	patch: UpdateAssistantConfigRequest,
) =>
	apiFetch<AssistantConfigResponse>(`/users/${userId}/assistant-config`, {
		method: 'PATCH',
		body: JSON.stringify(patch),
	});

export const resetAssistantConfig = (userId: string) =>
	apiFetch<AssistantConfigResponse>(
		`/users/${userId}/assistant-config/reset`,
		{ method: 'POST' },
	);

// ── Dashboard health ────────────────────────────────────────
export const getDashboardHealth = () =>
	apiFetch<DashboardHealthSnapshot>('/dashboard/health');

// ── MCP management ──────────────────────────────────────────
export const listMcpConnections = () =>
	apiFetch<McpConnectionView[]>('/mcp/connections');

export const createMcpConnection = (req: CreateMcpConnectionRequest) =>
	apiFetch<McpConnectionView>('/mcp/connections', {
		method: 'POST',
		body: JSON.stringify(req),
	});

export const updateMcpConnection = (
	id: string,
	patch: UpdateMcpConnectionRequest,
) =>
	apiFetch<McpConnectionView>(`/mcp/connections/${id}`, {
		method: 'PATCH',
		body: JSON.stringify(patch),
	});

export const deleteMcpConnection = (id: string) =>
	apiFetch<void>(`/mcp/connections/${id}`, { method: 'DELETE' });

export const listMyMcpServers = () =>
	apiFetch<McpServerSubscriptionView[]>('/mcp/my-servers');

export const upsertMcpSubscription = (
	id: string,
	req: UpsertSubscriptionRequest,
) =>
	apiFetch<McpServerSubscriptionView>(`/mcp/subscriptions/${id}`, {
		method: 'PUT',
		body: JSON.stringify(req),
	});

export const deleteMcpSubscription = (id: string) =>
	apiFetch<void>(`/mcp/subscriptions/${id}`, { method: 'DELETE' });

// Запускает OAuth-цикл: возвращает authorization_url, по которому уходит браузер.
export const startMcpOauth = (connectionId: string) =>
	apiFetch<McpOAuthStartResponse>(
		`/mcp/subscriptions/${connectionId}/oauth/start`,
		{ method: 'POST' },
	);

// Отключает OAuth: удаляет токены юзера для подключения. Идемпотентно.
export const disconnectMcpOauth = (connectionId: string) =>
	apiFetch<void>(`/mcp/subscriptions/${connectionId}/oauth`, {
		method: 'DELETE',
	});

export const discoverMcpPreview = (req: DiscoverPreviewRequest) =>
	apiFetch<DiscoverPreviewResponse>('/mcp/discover-preview', {
		method: 'POST',
		body: JSON.stringify(req),
	});

// ── Memory ──────────────────────────────────────────────────

/** Собирает query string; массивы — повторяемые параметры, пустые значения пропускаются. */
function buildQuery(params: Record<string, unknown>): string {
	const search = new URLSearchParams();
	for (const [key, value] of Object.entries(params)) {
		if (value === undefined || value === null || value === '') continue;
		if (Array.isArray(value)) {
			for (const item of value) search.append(key, String(item));
		} else {
			search.append(key, String(value));
		}
	}
	const qs = search.toString();
	return qs ? `?${qs}` : '';
}

export const getMemoryOverview = (userId: string) =>
	apiFetch<MemoryOverviewResponse>(`/users/${userId}/memory/overview`);

export const getMemoryContext = (userId: string) =>
	apiFetch<MemoryContextResponse>(`/users/${userId}/memory/context`);

export const listMemoryNotes = (userId: string, params: NotesListParams = {}) =>
	apiFetch<NotesPageResponse>(
		`/users/${userId}/memory/notes${buildQuery(params)}`,
	);

export const searchMemoryNotes = (
	userId: string,
	q: string,
	opts: { kinds?: NoteKind[]; subjects?: NoteSubject[]; limit?: number } = {},
) =>
	apiFetch<NoteSearchResponse>(
		`/users/${userId}/memory/notes/search${buildQuery({ q, ...opts })}`,
	);

export const createMemoryNote = (userId: string, req: CreateNoteRequest) =>
	apiFetch<NoteView>(`/users/${userId}/memory/notes`, {
		method: 'POST',
		body: JSON.stringify(req),
	});

export const updateMemoryNote = (
	userId: string,
	noteId: string,
	patch: UpdateNoteRequest,
) =>
	apiFetch<NoteView>(`/users/${userId}/memory/notes/${noteId}`, {
		method: 'PATCH',
		body: JSON.stringify(patch),
	});

export const reviseMemoryNote = (
	userId: string,
	noteId: string,
	content: string,
) =>
	apiFetch<NoteView>(`/users/${userId}/memory/notes/${noteId}/revise`, {
		method: 'POST',
		body: JSON.stringify({ content }),
	});

export const deleteMemoryNote = (userId: string, noteId: string) =>
	apiFetch<void>(`/users/${userId}/memory/notes/${noteId}`, {
		method: 'DELETE',
	});

export const getMemoryNoteOps = (userId: string, noteId: string) =>
	apiFetch<MemoryOpView[]>(`/users/${userId}/memory/notes/${noteId}/ops`);

export const listMemoryEntities = (userId: string) =>
	apiFetch<EntityView[]>(`/users/${userId}/memory/entities`);

export const listMemoryOps = (
	userId: string,
	opts: { pipelines?: MemoryPipeline[]; limit?: number; offset?: number } = {},
) =>
	apiFetch<OpsPageResponse>(`/users/${userId}/memory/ops${buildQuery(opts)}`);

export const getMemoryTurns = (
	userId: string,
	fromTurn: number,
	toTurn: number,
) =>
	apiFetch<TurnsRangeResponse>(
		`/users/${userId}/memory/turns${buildQuery({ from_turn: fromTurn, to_turn: toTurn })}`,
	);
