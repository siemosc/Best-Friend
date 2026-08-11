<script lang="ts">
	import '../app.css';
	import { page } from '$app/state';
	import { user } from '$lib/stores/user';

	const { children } = $props();

	type NavLink = { href: string; label: string };

	const baseLinks: NavLink[] = [
		{ href: '/dashboard', label: 'Dashboard' },
		{ href: '/assistant', label: 'Ассистент' },
		{ href: '/memory', label: 'Память' },
		{ href: '/mcp', label: 'MCP' },
	];
	const adminLinks: NavLink[] = [{ href: '/users', label: 'Пользователи' }];

	let links = $derived(
		$user?.role === 'admin' ? [...baseLinks, ...adminLinks] : baseLinks,
	);

	function isActive(href: string, pathname: string): boolean {
		if (href === '/') return pathname === '/';
		return pathname === href || pathname.startsWith(href + '/');
	}

	function userLabel(): string {
		if (!$user) return '';
		return $user.login ?? $user.user_id.slice(0, 8);
	}

	function userInitial(): string {
		const label = userLabel();
		return label ? label.charAt(0).toUpperCase() : '?';
	}
</script>

<div class="flex min-h-screen flex-col">
	<header
		class="sticky top-0 z-20 border-b border-gray-800/80 bg-gray-950/80 backdrop-blur supports-[backdrop-filter]:bg-gray-950/60"
	>
		<div class="mx-auto flex h-14 max-w-6xl items-center gap-6 px-4">
			<a
				href="/"
				class="flex shrink-0 items-center gap-2 rounded-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500/40"
			>
				<span
					class="inline-flex h-7 w-7 items-center justify-center rounded-lg bg-gradient-to-br from-indigo-500 to-violet-600 text-sm font-bold text-white shadow-glow"
					>B</span
				>
				<span class="font-semibold tracking-tight">BestFiend</span>
			</a>

			{#if $user}
				<nav class="flex min-w-0 flex-1 items-center gap-1 overflow-x-auto text-sm">
					{#each links as link (link.href)}
						{@const active = isActive(link.href, page.url.pathname)}
						<a
							href={link.href}
							class="whitespace-nowrap rounded-md px-3 py-1.5 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500/40
								{active
								? 'bg-indigo-500/15 text-white ring-1 ring-inset ring-indigo-500/25'
								: 'text-gray-400 hover:bg-white/5 hover:text-white'}"
							aria-current={active ? 'page' : undefined}
						>
							{link.label}
						</a>
					{/each}
				</nav>

				{@const profileActive = isActive('/profile', page.url.pathname)}
				<div class="flex shrink-0 items-center gap-2">
					<a
						href="/profile"
						aria-current={profileActive ? 'page' : undefined}
						title="Открыть профиль"
						class="flex items-center gap-2 rounded-lg border px-2 py-1 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500/40
							{profileActive
							? 'border-indigo-500/50 bg-indigo-500/10'
							: 'border-gray-800 bg-gray-900/60 hover:border-gray-700 hover:bg-gray-800/60'}"
					>
						<span
							class="inline-flex h-6 w-6 items-center justify-center rounded-full bg-gradient-to-br from-gray-600 to-gray-700 text-[11px] font-semibold text-gray-100"
							>{userInitial()}</span
						>
						<span class="max-w-[140px] truncate text-sm text-gray-200">{userLabel()}</span>
						{#if $user.role === 'admin'}
							<span class="badge border-indigo-500/30 bg-indigo-500/20 text-indigo-300">admin</span>
						{/if}
					</a>
					<a
						href="/logout"
						class="rounded-md px-3 py-1.5 text-sm text-gray-400 transition-colors hover:bg-gray-800/60 hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500/40"
						title="Выйти"
					>
						Выйти
					</a>
				</div>
			{:else}
				<nav class="ml-auto flex items-center gap-1 text-sm">
					<a
						href="/login"
						class="rounded-md px-3 py-1.5 text-gray-400 transition-colors hover:bg-gray-800/60 hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500/40"
						>Вход</a
					>
					<a
						href="/bind"
						class="rounded-md px-3 py-1.5 text-gray-400 transition-colors hover:bg-gray-800/60 hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500/40"
						>Первый вход</a
					>
				</nav>
			{/if}
		</div>
	</header>

	<main class="flex-1 animate-fade-in">
		{@render children()}
	</main>
</div>
