/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        azure: {
          50:  '#e6f2fc',
          100: '#cce5f9',
          200: '#99cbf3',
          300: '#66b1ed',
          400: '#3397e7',
          500: '#0078D4',  // Microsoft Azure blue
          600: '#0062ab',
          700: '#004a80',
          800: '#003156',
          900: '#00192b',
        },
      },
      fontFamily: {
        sans: ['"Segoe UI"', 'system-ui', '-apple-system', 'sans-serif'],
        mono: ['"Cascadia Code"', '"Fira Code"', 'monospace'],
      },
      animation: {
        'pulse-slow': 'pulse 3s cubic-bezier(0.4, 0, 0.6, 1) infinite',
        'fade-in':    'fadeIn 0.3s ease-in-out',
        'slide-up':   'slideUp 0.3s ease-out',
      },
      keyframes: {
        fadeIn:  { from: { opacity: '0' }, to: { opacity: '1' } },
        slideUp: { from: { transform: 'translateY(8px)', opacity: '0' },
                   to:   { transform: 'translateY(0)',   opacity: '1' } },
      },
      typography: (theme) => ({
        DEFAULT: {
          css: {
            maxWidth: 'none',
            color: theme('colors.gray.800'),
            code: { color: theme('colors.azure.700'), fontWeight: '500' },
            'code::before': { content: '""' },
            'code::after':  { content: '""' },
            h1: { color: theme('colors.gray.900') },
            h2: { color: theme('colors.gray.900') },
            h3: { color: theme('colors.gray.900') },
            a:  { color: theme('colors.azure.600') },
          },
        },
      }),
    },
  },
  plugins: [],
}
