import adapter from '@sveltejs/adapter-static';
import { vitePreprocess } from '@sveltejs/vite-plugin-svelte';

/** @type {import('@sveltejs/kit').Config} */
const config = {
	preprocess: vitePreprocess(),
	compilerOptions: {
		// Force runes mode для проекта. В node_modules — оставляем на усмотрение либ.
		runes: ({ filename }) =>
			filename.split(/[/\\]/).includes('node_modules') ? undefined : true,
	},
	kit: {
		// SPA-режим: single index.html, клиентский роутинг. Fallback на index.html
		// чтобы deep-links работали при прямом заходе.
		adapter: adapter({
			fallback: 'index.html',
			strict: false,
		}),
	},
};

export default config;
