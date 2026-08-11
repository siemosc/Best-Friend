// TS-типы, зеркало контрактов control_plane/contracts.py.
// Обновлять синхронно с backend-схемами (ручной процесс в MVP).

export type UserRole = 'user' | 'admin';
export type UserStatus = 'pending' | 'active' | 'banned';

export type UserResponse = {
	user_id: string;
	role: UserRole;
	status: UserStatus;
	telegram_chat_id: number | null;
	discord_user_id: string | null;
	login: string | null;
	timezone: string;
	city: string | null;
	country: string | null;
	created_at: string;
	updated_at: string | null;
};

export type LoginRequest = {
	login: string;
	password: string;
};

export type BindRequest = {
	code: string;
	login: string;
	password: string;
};

export type ApiErrorPayload = {
	error_code: string;
	detail: string;
};

export type AdminUpdateUserRequest = {
	role?: UserRole;
	status?: UserStatus;
	discord_user_id?: string;
};

export type UpdateProfileRequest = {
	timezone?: string;
	city?: string | null;
	country?: string | null;
};

export type ChangePasswordRequest = {
	current_password: string;
	new_password: string;
};

// ── Assistant config ─────────────────────────────────────────
// llm_custom_config — свободный jsonb (= столбец config таблицы models).
// Непустой → полная замена дефолтной модели графа; пустой → дефолт по MODEL_ID.
export type AssistantConfigResponse = {
	user_id: string;
	user_instruction: string;
	llm_custom_config: Record<string, unknown>;
	updated_at: string;
};

export type UpdateAssistantConfigRequest = {
	user_instruction?: string;
	llm_custom_config?: Record<string, unknown>;
};

// ── Dashboard health ────────────────────────────────────────
export type ServiceHealthStatus =
	| 'healthy'
	| 'unhealthy'
	| 'timeout'
	| 'unreachable';

export type ServiceHealth = {
	name: string;
	url: string;
	status: ServiceHealthStatus;
	latency_ms: number | null;
	error: string | null;
	checked_at: string;
};

export type DashboardLinks = {
	langfuse_url: string;
};

export type DashboardHealthSnapshot = {
	services: ServiceHealth[];
	links: DashboardLinks;
	fetched_at: string;
};

// ── MCP management ──────────────────────────────────────────
// Зеркало control_plane/mcp/contracts.py. connections — admin-CRUD;
// my-servers — per-user (connection-дефолты + subscription-оверрайды).
export type McpTransport = 'http_stream';
export type McpAuthType = 'none' | 'bearer' | 'oauth';
// Источник OAuth-клиента: preregistered — креды заданы админом; dcr — динамическая регистрация.
export type McpOAuthClientSource = 'preregistered' | 'dcr';
// Статус OAuth-подключения юзера к серверу.
export type McpOAuthStatus = 'not_connected' | 'connected' | 'expired';

export type McpConnectionView = {
	connection_id: string;
	name: string;
	url: string;
	transport: McpTransport;
	auth_type: McpAuthType;
	is_public: boolean;
	is_system: boolean;
	timeout_s: number;
	supports_parallel_tool_calls: boolean;
	oauth_client_id: string | null;
	oauth_client_source: McpOAuthClientSource | null;
	created_at: string;
	updated_at: string | null;
};

export type CreateMcpConnectionRequest = {
	name: string;
	url: string;
	transport?: McpTransport;
	auth_type?: McpAuthType;
	is_public?: boolean;
	timeout_s?: number;
	supports_parallel_tool_calls?: boolean;
	// Только при auth_type='oauth'. Пусто → DCR; секрет write-only.
	oauth_client_id?: string;
	oauth_client_secret?: string;
};

export type UpdateMcpConnectionRequest = {
	name?: string;
	url?: string;
	transport?: McpTransport;
	auth_type?: McpAuthType;
	is_public?: boolean;
	timeout_s?: number;
	supports_parallel_tool_calls?: boolean;
	oauth_client_id?: string;
	oauth_client_secret?: string;
};

// Ответ POST /mcp/subscriptions/{id}/oauth/start.
export type McpOAuthStartResponse = {
	authorization_url: string;
};

export type McpSubscriptionView = {
	enabled: boolean;
	auth_token: string | null;
	disabled_tools: string[];
	timeout_s: number | null;
	created_at: string;
};

export type McpServerSubscriptionView = {
	connection_id: string;
	name: string;
	url: string;
	transport: McpTransport;
	auth_type: McpAuthType;
	is_public: boolean;
	is_system: boolean;
	timeout_s: number;
	oauth_status: McpOAuthStatus | null;
	subscription: McpSubscriptionView | null;
};

export type UpsertSubscriptionRequest = {
	enabled?: boolean;
	auth_token?: string | null;
	disabled_tools?: string[];
	timeout_s?: number | null;
};

export type DiscoverPreviewRequest = {
	connection_id?: string;
	url?: string;
	auth_type?: McpAuthType;
	auth_token?: string | null;
};

export type DiscoveredToolView = {
	name: string;
	description: string;
};

export type DiscoverPreviewFailureView = {
	kind: 'timeout' | 'auth' | 'unreachable' | 'protocol';
	message: string;
};

export type DiscoverPreviewResponse = {
	connection_id: string | null;
	name: string;
	instructions: string | null;
	tools: DiscoveredToolView[];
	failure: DiscoverPreviewFailureView | null;
};

// ── Memory ──────────────────────────────────────────────────
// Зеркало memory/http/contracts.py. Заметка — атом памяти; subject — о ком
// знание; pinned/in_journal — постоянный контекст модели.
export type NoteKind =
	| 'observation'
	| 'fact'
	| 'preference'
	| 'rule'
	| 'reflection'
	| 'entity_card'
	| 'period_summary';
export type NoteSubject = 'user' | 'agent' | 'world';
export type NoteStatus = 'active' | 'superseded' | 'contradicted';
export type PinSection = 'identity' | 'preferences' | 'relationships' | 'rules';
export type MemoryPipeline =
	| 'observer'
	| 'reconciler'
	| 'reflector'
	| 'tool'
	| 'sleep'
	| 'ui';

export type NoteEntityRef = {
	id: string;
	name: string;
};

export type NoteView = {
	id: string;
	kind: NoteKind;
	subject: NoteSubject | null;
	content: string;
	event_time: string | null;
	observed_at: string;
	status: NoteStatus;
	pinned: boolean;
	pin_section: PinSection | null;
	in_journal: boolean;
	journal_weight: number;
	source_turn_start: number | null;
	source_turn_end: number | null;
	use_count: number;
	entities: NoteEntityRef[];
};

export type NotesPageResponse = {
	items: NoteView[];
	total: number;
	limit: number;
	offset: number;
};

export type NoteSearchResponse = {
	items: NoteView[];
	gate_passed: boolean;
};

export type MemoryContextResponse = {
	profile: NoteView[];
	journal: NoteView[];
};

export type CreateNoteRequest = {
	kind: 'fact' | 'preference' | 'rule';
	subject: NoteSubject;
	content: string;
	pin?: boolean;
	pin_section?: PinSection | null;
};

export type UpdateNoteRequest = {
	pinned?: boolean;
	pin_section?: PinSection | null;
	in_journal?: boolean;
	subject?: NoteSubject;
};

export type NotesListParams = {
	kinds?: NoteKind[];
	subjects?: NoteSubject[];
	statuses?: NoteStatus[];
	pinned?: boolean;
	in_journal?: boolean;
	entity_id?: string;
	q?: string;
	limit?: number;
	offset?: number;
};

export type EntityView = {
	id: string;
	canonical_name: string;
	aliases: string[];
	notes_count: number;
};

export type MemoryOpView = {
	id: number;
	pipeline: MemoryPipeline;
	op: string;
	note_id: string | null;
	target_note_id: string | null;
	detail: string | null;
	created_at: string;
	note_content: string | null;
	target_note_content: string | null;
};

export type OpsPageResponse = {
	items: MemoryOpView[];
	total: number;
	limit: number;
	offset: number;
};

export type TurnView = {
	id: number;
	created_at: string;
	rendered: string;
};

export type TurnsRangeResponse = {
	items: TurnView[];
};

export type MemoryOverviewResponse = {
	by_kind: Record<string, number>;
	by_subject: Record<string, number>;
	by_status: Record<string, number>;
	journal_count: number;
	pinned_count: number;
	entities_count: number;
};
