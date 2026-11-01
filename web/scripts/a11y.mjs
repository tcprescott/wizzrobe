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
 */
import { spawn } from 'node:child_process'
import { mkdirSync, readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { chromium } from 'playwright'

const here = dirname(fileURLToPath(import.meta.url))
const root = join(here, '..')
const require = createRequire(import.meta.url)
const axeSource = readFileSync(require.resolve('axe-core/axe.min.js'), 'utf8')
const fixtures = JSON.parse(readFileSync(join(here, 'a11y-fixtures.json'), 'utf8'))

const PORT = 4173
const BASE = `http://localhost:${PORT}`
const PHONES = (process.env.A11Y_WIDTHS || '360').split(',').map((w) => Number(w.trim()))
const DESKTOP = { width: 1440, height: 900 }
const SCHEMES = ['light', 'dark']
const OUT = join(root, 'a11y-report')
mkdirSync(OUT, { recursive: true })

const SESSION = JSON.stringify({ access: 'fixture-access', refresh: 'fixture-refresh', expiresAt: 4102444800000 })

/** `signedIn: false` loads with no session; `act` puts the page in a state worth checking. */
const TARGETS = [
  { name: 'sign-in', path: '/sign-in', signedIn: false },
  { name: 'kit', path: '/kit', signedIn: false },
  { name: 'kit-dialog', path: '/kit', signedIn: false, act: (p) => p.getByRole('button', { name: 'Open dialog' }).click(), wait: 'role=dialog' },
  { name: 'kit-menu', path: '/kit', signedIn: false, act: (p) => p.getByRole('button', { name: 'More for this row' }).click(), wait: 'role=menu' },
  { name: 'communities', path: '/' },
  { name: 'home', path: '/t/sgl/home' },
  { name: 'home-event', path: '/t/sgl/e/sgl26/home' },
  { name: 'schedule', path: '/t/sgl/schedule' },
  { name: 'home-pick-time', path: '/t/sgl/home', act: (p) => p.getByRole('button', { name: 'Pick a time' }).click(), wait: 'role=dialog' },
  { name: 'home-review', path: '/t/sgl/home', act: (p) => p.getByRole('button', { name: 'Review' }).click(), wait: 'role=dialog' },
  { name: 'home-running-late', path: '/t/sgl/home', act: (p) => p.getByRole('button', { name: 'Running late' }).first().click(), wait: 'role=dialog' },
  { name: 'event', path: '/t/sgl/event' },
  { name: 'work', path: '/t/sgl/work' },
  { name: 'me', path: '/t/sgl/me' },
  { name: 'me-sign-out', path: '/t/sgl/me', act: (p) => p.getByRole('button', { name: 'Sign out' }).click(), wait: 'role=dialog' },
  { name: 'picker', path: '/t/sgl/home', act: (p) => p.getByRole('button', { name: /Change event or community/ }).click(), wait: 'role=menu' },
  { name: 'player-no-work', path: '/t/second/home' },
  { name: 'not-a-member', path: '/t/elsewhere/home' },
  { name: 'not-found', path: '/nowhere' },
]

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

// "{{now+42m}}" in a fixture becomes an instant that far from now, so countdowns and "Today" render.
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
  await context.route('**/api/**', (route) => {
    const path = new URL(route.request().url()).pathname
    const body = fixtures[path]
    if (body === undefined) return route.fulfill({ status: 404, json: { detail: 'Not in the a11y fixtures' } })
    return route.fulfill({ status: 200, json: withTimes(body) })
  })
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
  const page = await context.newPage()
  const problems = []
  page.on('pageerror', (err) => problems.push(`page error: ${err.message}`))
  await page.goto(BASE + target.path, { waitUntil: 'networkidle' })
  await page.locator('h1').first().waitFor({ timeout: 10000 })
  if (target.act) {
    await target.act(page)
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
  for (const target of TARGETS) {
    for (const { label, viewport } of viewports) {
      for (const scheme of SCHEMES) {
        const problems = await check(browser, target, scheme, viewport, label)
        const tag = `${target.name} @ ${label} ${scheme}`
        if (problems.length) {
          failures += problems.length
          console.log(`✗ ${tag}`)
          for (const problem of problems) console.log(`    ${problem}`)
        } else {
          console.log(`✓ ${tag}`)
        }
      }
    }
  }
} finally {
  await browser.close()
  preview.kill()
}
if (failures) {
  console.log(`\n${failures} problem(s). docs/style-guide.md says how to fix contrast and naming.`)
  process.exit(1)
}
console.log('\nAll clear.')
