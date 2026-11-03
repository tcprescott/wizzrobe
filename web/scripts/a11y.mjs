/*
 * Accessibility, contrast and phone-layout gate for the new app.
 *
 * Serves the production build (`npm run build` first) with `vite preview`,
 * stubs the API with fixtures so no backend is needed, and checks every
 * target in light and dark, on a phone and on desktop:
 *
 *   1. axe-core's WCAG 2.1 A/AA rules, color-contrast included (chips here are
 *      real backgrounds, not the inset shadows the old app's scan works around).
 *   2. No sideways scroll at phone width.
 *
 * Screenshots of every pass land in a11y-report/. Exit code 1 on any failure.
 *
 * Usage: npm run build && npm run a11y
 *   A11Y_WIDTHS=360,393,412,440   phone widths to check (default 360)
 *   PW_CHROMIUM=/path/to/chrome   use an already-installed Chromium
 *   A11Y_ONLY=me-notifications    only targets whose name starts with this
 *   A11Y_JOBS=4                   checks run at once (default 4)
 */
import { spawn } from 'node:child_process'
import { mkdirSync, readdirSync, readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { dirname, join } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { chromium } from 'playwright'

const here = dirname(fileURLToPath(import.meta.url))
const root = join(here, '..')
const require = createRequire(import.meta.url)
const axeSource = readFileSync(require.resolve('axe-core/axe.min.js'), 'utf8')

const GUIDE_PDF = readFileSync(join(root, '..', 'application', 'event_info', 'content', 'default', 'Event-Guide-Dev-Fixture.pdf'))

const PORT = 4173
const BASE = `http://localhost:${PORT}`
const JOBS = Math.max(1, Number(process.env.A11Y_JOBS) || 4)
const PHONES = (process.env.A11Y_WIDTHS || '360').split(',').map((w) => Number(w.trim()))
const DESKTOP = { width: 1440, height: 900 }
const SCHEMES = ['light', 'dark']
const OUT = join(root, 'a11y-report')
mkdirSync(OUT, { recursive: true })

const SESSION = JSON.stringify({ access: 'fixture-access', refresh: 'fixture-refresh', expiresAt: 4102444800000 })

/**
 * Each screen's checks and stub API responses live in a11y/<screen>.mjs, which
 * exports `targets` and `fixtures`; every file in that folder is picked up, so
 * a new screen adds a file instead of editing a shared list.
 *
 * A target: `signedIn: false` loads with no session; `act` puts the page in a
 * state worth checking and `wait` is what to wait for after it; `native` runs
 * it as the store app on that platform.
 *
 * A fixture: the JSON body served for that API path. "{{now+42m}}" in it
 * becomes an instant that far from now, so countdowns and "Today" render.
 */
const TARGETS = []
const fixtures = {}
const screens = join(here, 'a11y')
for (const file of readdirSync(screens).filter((f) => f.endsWith('.mjs')).sort()) {
  const screen = await import(pathToFileURL(join(screens, file)).href)
  for (const target of screen.targets ?? []) {
    if (TARGETS.some((t) => t.name === target.name)) throw new Error(`a11y/${file}: target "${target.name}" is already defined`)
    TARGETS.push(target)
  }
  for (const [path, body] of Object.entries(screen.fixtures ?? {})) {
    if (path in fixtures) throw new Error(`a11y/${file}: fixture ${path} is already defined in another screen file`)
    fixtures[path] = body
  }
}

function startPreview() {
  const vite = join(root, 'node_modules', 'vite', 'bin', 'vite.js')
  const child = spawn(process.execPath, [vite, 'preview', '--port', String(PORT), '--strictPort'], { cwd: root, stdio: 'pipe' })
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('vite preview did not start')), 30000)
    child.stdout.on('data', (chunk) => {
      if (String(chunk).includes(String(PORT))) {
        clearTimeout(timer)
        resolve(child)
      }
    })
    child.on('exit', (code) => reject(new Error(`vite preview exited (${code})`)))
  })
}

const UNITS = { m: 60e3, h: 3600e3, d: 86400e3 }
function withTimes(value) {
  if (typeof value === 'string') {
    const m = /^\{\{now([+-]\d+)([mhd])\}\}$/.exec(value)
    return m ? new Date(Date.now() + Number(m[1]) * UNITS[m[2]]).toISOString() : value
  }
  if (Array.isArray(value)) return value.map(withTimes)
  if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value).map(([k, v]) => [k, withTimes(v)]))
  return value
}

async function stubApi(context) {
  await context.route('https://player.twitch.tv/**', (route) =>
    route.fulfill({ status: 200, contentType: 'text/html', body: '<!doctype html><html lang="en"><title>Stream</title><body></body></html>' }),
  )
  await context.route('**/api/**', (route) => {
    const path = new URL(route.request().url()).pathname
    if (path.endsWith('/guide.pdf')) return route.fulfill({ status: 200, contentType: 'application/pdf', body: GUIDE_PDF })
    const body = fixtures[path]
    if (body === undefined) return route.fulfill({ status: 404, json: { detail: 'Not in the a11y fixtures' } })
    return route.fulfill({ status: 200, json: withTimes(body) })
  })
}

/** Runs in the page: makes @capacitor/core report a store app, with the native calls the screens make. */
function nativeShim(platform) {
  const answers = {
    App: { addListener: {}, exitApp: {} },
    Browser: { open: {}, close: {} },
    Device: { getInfo: { model: 'iPhone 16' } },
    PushNotifications: {
      addListener: {},
      checkPermissions: { receive: 'prompt' },
      requestPermissions: { receive: 'granted' },
      register: {},
      unregister: {},
      createChannel: {},
    },
  }
  window.CapacitorCustomPlatform = { name: platform, plugins: {} }
  window.Capacitor = {
    PluginHeaders: Object.entries(answers).map(([name, methods]) => ({
      name,
      methods: Object.keys(methods).map((m) => ({ name: m, rtype: 'promise' })),
    })),
    nativePromise: async (plugin, method) => answers[plugin]?.[method] ?? {},
    nativeCallback: () => '',
  }
}

async function check(browser, target, scheme, viewport, label) {
  const context = await browser.newContext({ viewport, colorScheme: scheme, reducedMotion: 'reduce' })
  await stubApi(context)
  await context.addInitScript(
    ([session, theme]) => {
      if (session) localStorage.setItem('wiz.session', session)
      localStorage.setItem('wiz.theme', theme)
    },
    [target.signedIn === false ? null : SESSION, scheme],
  )
  if (target.native) await context.addInitScript(nativeShim, target.native)
  const page = await context.newPage()
  const problems = []
  page.on('pageerror', (err) => problems.push(`page error: ${err.message}`))
  await page.goto(BASE + target.path, { waitUntil: 'networkidle' })
  await page.locator('h1').first().waitFor({ timeout: 10000 })
  if (target.act) await target.act(page)
  if (target.wait) {
    await page.locator(target.wait).first().waitFor({ timeout: 5000 })
    await page.waitForTimeout(250)
  }
  await page.evaluate(axeSource)
  const result = await page.evaluate(async () =>
    // eslint-disable-next-line no-undef
    axe.run(document, { runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] } }),
  )
  for (const violation of result.violations) {
    for (const node of violation.nodes) {
      problems.push(`${violation.id}: ${node.target.join(' ')} — ${node.failureSummary?.split('\n').slice(1).join(' ').trim()}`)
    }
  }
  if (viewport.width < 768) {
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
    if (overflow > 0) problems.push(`page scrolls sideways by ${overflow}px`)
  }
  await page.screenshot({ path: join(OUT, `${target.name}-${label}-${scheme}.png`), fullPage: true })
  await context.close()
  return problems
}

const preview = await startPreview()
const browser = await chromium.launch(process.env.PW_CHROMIUM ? { executablePath: process.env.PW_CHROMIUM } : {})
let failures = 0
try {
  const viewports = [
    ...PHONES.map((width) => ({ label: `phone${width}`, viewport: { width, height: 780 } })),
    { label: 'desktop', viewport: DESKTOP },
  ]
  const runs = []
  for (const target of TARGETS.filter((t) => t.name.startsWith(process.env.A11Y_ONLY || ''))) {
    for (const { label, viewport } of viewports) {
      for (const scheme of SCHEMES) runs.push({ target, label, viewport, scheme })
    }
  }
  // Each check has its own browser context, so they run side by side; reports print in order.
  const results = new Array(runs.length)
  let next = 0
  let printed = 0
  const flush = () => {
    while (printed < runs.length && results[printed]) {
      const { target, label, scheme } = runs[printed]
      const problems = results[printed]
      const tag = `${target.name} @ ${label} ${scheme}`
      if (problems.length) {
        failures += problems.length
        console.log(`✗ ${tag}`)
        for (const problem of problems) console.log(`    ${problem}`)
      } else {
        console.log(`✓ ${tag}`)
      }
      printed++
    }
  }
  const worker = async () => {
    while (next < runs.length) {
      const i = next++
      const { target, label, viewport, scheme } = runs[i]
      results[i] = await check(browser, target, scheme, viewport, label)
      flush()
    }
  }
  await Promise.all(Array.from({ length: JOBS }, worker))
} finally {
  await browser.close()
  preview.kill()
}
if (failures) {
  console.log(`\n${failures} problem(s). docs/style-guide.md says how to fix contrast and naming.`)
  process.exit(1)
}
console.log('\nAll clear.')
