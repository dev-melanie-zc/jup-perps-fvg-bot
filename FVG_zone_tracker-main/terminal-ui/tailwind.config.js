/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      fontFamily: {
        mono: ['"Courier New"', 'Courier', 'monospace'],
      },
      colors: {
        term: {
          bg:      '#0a0e14',
          panel:   '#0d1219',
          border:  '#1e2a38',
          border2: '#253040',
          green:   '#39d353',
          green2:  '#22c55e',
          green3:  '#16a34a',
          greenbg: 'rgba(34,197,94,0.07)',
          red:     '#e05260',
          redbg:   'rgba(224,82,96,0.08)',
          blue:    '#4a90d9',
          bluebg:  'rgba(74,144,217,0.18)',
          grey:    '#4a5568',
          text:    '#b8c7d8',
          muted:   '#5a6a7e',
          menubar: '#1a1f2e',
          menubg:  '#141920',
        },
      },
      fontSize: {
        '2xs': '0.65rem',
        '3xs': '0.58rem',
      },
    },
  },
  plugins: [],
}
