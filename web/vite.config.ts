import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'
import svgr from 'vite-plugin-svgr'

// In dev the app and the API share an origin through this proxy, so sign-in and
// API calls need no CORS. Point it at a running backend with WIZZROBE_API.
const backend = process.env.WIZZROBE_API ?? 'http://localhost:8080'
const proxied = ['/api', '/oauth', '/.well-known/oauth-authorization-server']

export default defineConfig({
  plugins: [react(), svgr()],
  server: {
    port: 5173,
    strictPort: true,
    proxy: Object.fromEntries(proxied.map((path) => [path, { target: backend, changeOrigin: false }])),
  },
  build: {
    sourcemap: true,
  },
  test: {
    environment: 'jsdom',
    include: ['src/**/*.test.{ts,tsx}'],
  },
})
