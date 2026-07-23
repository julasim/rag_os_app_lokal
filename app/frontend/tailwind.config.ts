import type { Config } from 'tailwindcss'

const config: Config = {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        surface: '#ffffff',
        app: '#fafafa',
        muted: '#f5f5f5',
        'border-default': '#ededed',
        'border-subtle': '#f5f5f5',
        'text-primary': '#111111',
        'text-secondary': '#262626',
        'text-muted': '#525252',
        'text-subtle': '#737373',
        'text-faint': '#a3a3a3',
        accent: '#111111',
        'success-bg': '#ecfdf5',
        'success-fg': '#047857',
        'success-dot': '#10b981',
        'warning-bg': '#fffbeb',
        'warning-fg': '#92400e',
        'warning-dot': '#f59e0b',
        'danger-bg': '#fef2f2',
        'danger-fg': '#991b1b',
        'danger-dot': '#ef4444',
      },
      fontFamily: {
        // Helvetica zuerst (SIMA-Corporate-Design), rein lokal — kein Web-Font-Download.
        // Windows substituiert Helvetica durch Arial (metrisch identisch).
        sans: ['"Helvetica Neue"', 'Helvetica', 'Arial', 'sans-serif'],
        mono: ['ui-monospace', '"SF Mono"', '"JetBrains Mono"', 'monospace'],
      },
      borderRadius: {
        card: '8px',
        btn: '6px',
        tag: '4px',
        chip: '14px',
      },
      spacing: {
        '4.5': '18px',
      },
    },
  },
  plugins: [],
}

export default config
