import { createHash } from 'node:crypto'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'
import react from '@vitejs/plugin-react'
import { loadEnv, type Plugin } from 'vite'
import { defineConfig } from 'vitest/config'
import svgr from 'vite-plugin-svgr'
import { DEFAULT_APP_ID } from './src/native/appId'

// In dev the app and the API share an origin through this proxy, so sign-in and
// API calls need no CORS. Point it at a running backend with WIZZROBE_API.
const backend = process.env.WIZZROBE_API ?? 'http://localhost:8000'
const proxied = ['/api', '/oauth', '/.well-known/oauth-authorization-server']

function publicFiles(dir: string): string[] {
  const out: string[] = []
  const walk = (at: string) => {
    for (const name of readdirSync(at)) {
      const path = join(at, name)
      if (statSync(path).isDirectory()) walk(path)
      else out.push('/' + relative(dir, path).split('\\').join('/'))
    }
  }
  walk(dir)
  return out
}

/** Writes dist/sw.js from sw/sw.js with this build's file list baked in. */
function serviceWorker(apiBase: string): Plugin {
  let publicDir = ''
  return {
    name: 'wiz-service-worker',
    apply: 'build',
    configResolved(config) {
      publicDir = config.publicDir
    },
    generateBundle(_options, bundle) {
      const built = Object.keys(bundle)
        // Every browser with service workers reads woff2, so the .woff fallbacks stay out.
        .filter((name) => !name.endsWith('.map') && !name.endsWith('.woff') && name !== 'index.html')
        .map((name) => '/' + name)
      const files = [...built, ...(publicDir ? publicFiles(publicDir) : [])].sort()
      const build = createHash('sha256').update(files.join('\n')).digest('hex').slice(0, 12)
      const source = readFileSync(new URL('./sw/sw.js', import.meta.url), 'utf8')
        .replace("'__WIZ_BUILD__'", JSON.stringify(build))
        .replace('__WIZ_PRECACHE__', JSON.stringify(files))
        .replace("'__WIZ_API_BASE__'", JSON.stringify(apiBase.replace(/\/$/, '')))
      this.emitFile({ type: 'asset', fileName: 'sw.js', source })
    },
  }
}

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), 'VITE_')
  return {
    plugins: [react(), svgr(), serviceWorker(env.VITE_API_BASE ?? '')],
    define: {
      'import.meta.env.VITE_APP_ID': JSON.stringify(process.env.WIZZROBE_APP_ID || DEFAULT_APP_ID),
    },
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
  }
})
