// Словари подписей и цветов сущностей памяти + утилиты форматирования.
// Цвета — tailwind-классы бейджей в тёмной теме.

import type {
	MemoryPipeline,
	NoteKind,
	NoteStatus,
	NoteSubject,
	PinSection,
} from '$lib/types';

// Базовый вид бейджа задан классом `.badge` в app.css; цвет добавляется отдельно.
export const BADGE_BASE = 'badge';

export const KIND_LABELS: Record<NoteKind, string> = {
	observation: 'наблюдение',
	fact: 'факт',
	preference: 'предпочтение',
	rule: 'правило',
	reflection: 'рефлексия',
	entity_card: 'карточка',
	period_summary: 'сводка',
};

export const KIND_BADGE: Record<NoteKind, string> = {
	observation: 'bg-sky-500/15 text-sky-300 border-sky-700/40',
	fact: 'bg-blue-500/15 text-blue-300 border-blue-700/40',
	preference: 'bg-emerald-500/15 text-emerald-300 border-emerald-700/40',
	rule: 'bg-violet-500/15 text-violet-300 border-violet-700/40',
	reflection: 'bg-amber-500/15 text-amber-300 border-amber-700/40',
	entity_card: 'bg-pink-500/15 text-pink-300 border-pink-700/40',
	period_summary: 'bg-cyan-500/15 text-cyan-300 border-cyan-700/40',
};

export const SUBJECT_LABELS: Record<NoteSubject, string> = {
	user: 'пользователь',
	agent: 'агент',
	world: 'мир',
};

export const SUBJECT_BADGE: Record<NoteSubject, string> = {
	user: 'bg-emerald-500/10 text-emerald-400 border-emerald-800/40',
	agent: 'bg-indigo-500/10 text-indigo-400 border-indigo-800/40',
	world: 'bg-sky-500/10 text-sky-400 border-sky-800/40',
};

export const STATUS_LABELS: Record<NoteStatus, string> = {
	active: 'активна',
	superseded: 'заменена',
	contradicted: 'противоречие',
};

export const STATUS_BADGE: Record<NoteStatus, string> = {
	active: 'bg-gray-700/40 text-gray-300 border-gray-700',
	superseded: 'bg-gray-700/40 text-gray-400 border-gray-700',
	contradicted: 'bg-orange-500/15 text-orange-300 border-orange-700/40',
};

export const PIN_SECTION_LABELS: Record<PinSection, string> = {
	identity: 'идентичность',
	preferences: 'предпочтения',
	relationships: 'отношения',
	rules: 'правила',
};

export const PIPELINE_LABELS: Record<MemoryPipeline, string> = {
	observer: 'Observer',
	reconciler: 'Reconciler',
	reflector: 'Reflector',
	tool: 'тулза',
	sleep: 'sleep',
	ui: 'вручную',
};

export const PIPELINE_BADGE: Record<MemoryPipeline, string> = {
	observer: 'bg-blue-500/15 text-blue-300 border-blue-700/40',
	reconciler: 'bg-amber-500/15 text-amber-300 border-amber-700/40',
	reflector: 'bg-purple-500/15 text-purple-300 border-purple-700/40',
	tool: 'bg-emerald-500/15 text-emerald-300 border-emerald-700/40',
	sleep: 'bg-cyan-500/15 text-cyan-300 border-cyan-700/40',
	ui: 'bg-pink-500/15 text-pink-300 border-pink-700/40',
};

export const OP_LABELS: Record<string, string> = {
	add: 'добавлено',
	supersede: 'заменено',
	noop: 'пропуск',
	contradict: 'противоречие',
	evict: 'вытеснено из журнала',
	reflect: 'консолидация',
	pin: 'закреплено',
	unpin: 'откреплено',
	demote: 'демоция из профиля',
	revise: 'правка',
	merge: 'слияние дублей',
	delete: 'удалено',
	edit: 'правка флагов',
};

export function formatDateTime(iso: string): string {
	return new Date(iso).toLocaleString('ru-RU', {
		day: '2-digit',
		month: '2-digit',
		year: '2-digit',
		hour: '2-digit',
		minute: '2-digit',
	});
}
