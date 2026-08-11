/** @type {import('tailwindcss').Config} */
export default {
	content: ['./src/**/*.{html,js,svelte,ts}'],
	theme: {
		extend: {
			boxShadow: {
				// мягкая тень карточки в тёмной теме — глубина без визуального шума
				card: '0 1px 2px 0 rgb(0 0 0 / 0.35), 0 2px 8px -2px rgb(0 0 0 / 0.30)',
				'card-hover': '0 2px 4px 0 rgb(0 0 0 / 0.40), 0 8px 24px -6px rgb(0 0 0 / 0.45)',
				// свечение фирменного акцента для primary-элементов
				glow: '0 8px 24px -10px rgb(79 70 229 / 0.55)',
			},
			keyframes: {
				'fade-in': {
					from: { opacity: '0' },
					to: { opacity: '1' },
				},
				'fade-in-up': {
					from: { opacity: '0', transform: 'translateY(6px)' },
					to: { opacity: '1', transform: 'translateY(0)' },
				},
				'slide-in-right': {
					from: { transform: 'translateX(100%)' },
					to: { transform: 'translateX(0)' },
				},
			},
			animation: {
				'fade-in': 'fade-in 0.2s ease-out both',
				'fade-in-up': 'fade-in-up 0.25s ease-out both',
				'slide-in-right': 'slide-in-right 0.24s cubic-bezier(0.22, 1, 0.36, 1) both',
			},
		},
	},
	plugins: [],
};
