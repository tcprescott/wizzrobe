/*
 * Reusable browser-validation harness for Wizzrobe (MOCK_DISCORD mode).
 *
 * Drives the running app with Playwright: logs in via the mock user picker,
 * visits each target page (optionally clicking a Quasar tab), screenshots it,
 * extracts the text of a selector, and reports any console / page errors — the
 * only way to validate client-side Vue/Quasar slot templates that Python render
 * tests can't reach.
 *
 * Prereqs: `bash scripts/setup_env.sh`, then boot (`./start.sh validate`) and seed
 * (`poetry run python scripts/seed_dev.py`). See the `ui-validation` skill.
 *
 * Usage:
 *   NODE_PATH=$(npm root -g) node scripts/ui_smoke.js config.json
 *
 * config.json (all fields optional except targets):
 *   {
 *     "baseUrl": "http://127.0.0.1:8000",
 *     "loginAs": "staff_user",          // username in the mock picker
 *     "tenant":  "default",              // /t/<slug> prefix for login + targets
 *     "outDir":  "/tmp/ui-smoke",        // screenshots + text dumps land here
 *     "chrome":  "/opt/pw-browsers/.../chrome",  // auto-detected if omitted
 *     "failMarkers": ["Custom failure text"],  // added to the built-in list
 *     "targets": [
 *       { "name": "admin-schedule", "path": "/admin", "tab": "Schedule", "selector": ".match-table" }
 *     ]
 *   }
 *
 * Tabs: prefer addressing a tab by its **section URL** (`/admin/users`,
 * `/home/profile` — theme.base.tab_slug derives the slug from the label) over
 * `"tab": "Users"`. The section route makes that tab the page default, so its
 * content is built during the initial render; clicking depends on the label
 * being reachable, which it isn't for a grouped admin drawer or an overflowed
 * tab bar. A requested tab that can't be clicked is reported as a failure.
 *
 * Failure detection: besides console/page errors (noisy — a pre-existing
 * ui.image warning always fires), each target is checked for a *rendered*
 * failure: a 5xx navigation, or the text a broken page/tab leaves behind (the
 * BaseLayout tab guard renders "This section failed to load…" when a tab's
 * build raises — e.g. an ungated surface calling a feature-gated service).
 * Those are reported under "=== rendered failures ===" and exit the process
 * non-zero, so the harness can drive a sweep (see scripts/ui_flag_sweep.sh).
 * Set "failOnFailure": false to keep the old always-exit-0 behaviour.
 *
 * Multitenancy: the app serves every community under /t/<slug>/… — a bare
 * /admin 404s ("only available within a community") and a bare / is the
 * community picker, not a tenant home. Set "tenant" (dev slug: "default") and
 * the harness prefixes the mock login and each target path with /t/<slug>. A
 * target already starting with "/t/" is left as-is; a target marked
 * "platform": true (e.g. the /platform surface or the bare "/" picker) is never
 * prefixed. Omit "tenant" to drive the bare platform host as before.
 */
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');
const { execSync } = require('child_process');

function findChrome(explicit) {
  if (explicit && fs.existsSync(explicit)) return explicit;
  try {
    const hit = execSync(
      "find /opt/pw-browsers -type f -name chrome 2>/dev/null | head -n1",
    ).toString().trim();
    if (hit) return hit;
  } catch (_) {}
  return undefined;
}

const DEFAULT_FAIL_MARKERS = [
  'This section failed to load',
  'Internal Server Error',
  'Traceback (most recent call last)',
];

async function clickTab(page, name) {
  if (!name) return false;
  for (const s of [`[role="tab"]:has-text("${name}")`, `.q-tab:has-text("${name}")`,
                   `div.q-tab__label:has-text("${name}")`]) {
    const el = page.locator(s).first();
    if (await el.count()) { await el.click().catch(() => {}); return true; }
  }
  return false;
}

(async () => {
  const cfgPath = process.argv[2];
  const cfg = cfgPath ? JSON.parse(fs.readFileSync(cfgPath, 'utf8')) : {};
  const baseUrl = cfg.baseUrl || 'http://127.0.0.1:8000';
  const loginAs = cfg.loginAs || 'staff_user';
  const outDir = cfg.outDir || '/tmp/ui-smoke';
  const targets = cfg.targets || [{ name: 'home', path: '/', selector: 'body' }];
  const tprefix = cfg.tenant ? `/t/${cfg.tenant}` : '';
  const withTenant = (p, target) => {
    if (!tprefix || (target && target.platform) || p.startsWith('/t/')) return p;
    return tprefix + p;
  };
  fs.mkdirSync(outDir, { recursive: true });

  const browser = await chromium.launch({
    executablePath: findChrome(cfg.chrome),
    args: ['--no-sandbox'],
  });
  const ctx = await browser.newContext({ viewport: cfg.viewport || { width: 1500, height: 1100 } });
  const page = await ctx.newPage();
  const errors = [];
  page.on('pageerror', e => errors.push('PAGEERROR: ' + e.message));
  page.on('console', m => { if (m.type() === 'error') errors.push('CONSOLE.ERROR: ' + m.text().slice(0, 200)); });

  // The picker is a <table> on desktop and .wiz-grid-card cards on a phone: match either.
  await page.goto(`${baseUrl}${withTenant('/login')}`, { waitUntil: 'networkidle' });
  await page.waitForTimeout(800);
  const row = page.locator('tr, .wiz-grid-card', { hasText: loginAs }).first();
  await row.getByRole('button', { name: 'Log in as' }).click();
  await page.waitForTimeout(1500);
  console.log(`logged in as ${loginAs} -> ${page.url()}`);

  const failMarkers = DEFAULT_FAIL_MARKERS.concat(cfg.failMarkers || []);
  const failures = [];

  for (const t of targets) {
    // A hung navigation fails this target, not the whole sweep.
    let resp;
    try {
      resp = await page.goto(`${baseUrl}${withTenant(t.path, t)}`, { waitUntil: 'networkidle' });
    } catch (e) {
      failures.push(`[${t.name}] navigation failed: ${e.message.split('\n')[0]}`);
      console.log(`  [${t.name}] navigation failed: ${e.message.split('\n')[0]}`);
      continue;
    }
    if (resp && resp.status() >= 500) failures.push(`[${t.name}] HTTP ${resp.status()} on ${t.path}`);
    await page.waitForTimeout(1200);
    if (t.tab) {
      // A missing tab must fail: the screenshot would show the default tab instead.
      const clicked = await clickTab(page, t.tab);
      console.log(`  [${t.name}] tab "${t.tab}" clicked: ${clicked}`);
      if (!clicked) failures.push(`[${t.name}] tab "${t.tab}" not found on ${t.path}`);
    }
    await page.waitForTimeout(1500);
    const body = await page.evaluate(() => document.body ? document.body.innerText : '');
    for (const marker of failMarkers) {
      if (body.includes(marker)) failures.push(`[${t.name}] rendered failure: "${marker}" (${t.path}${t.tab ? ` › ${t.tab}` : ''})`);
    }
    const shot = path.join(outDir, `${t.name}.png`);
    await page.screenshot({ path: shot, fullPage: true });
    const text = await page.evaluate((sel) => {
      const el = document.querySelector(sel);
      return el ? el.innerText : `(selector ${sel} not found)`;
    }, t.selector || 'body');
    fs.writeFileSync(path.join(outDir, `${t.name}.txt`), text);
    console.log(`  [${t.name}] screenshot=${shot} textlen=${text.length}`);
    console.log(text.slice(0, 600));
    console.log('  ---');
  }

  console.log('=== console/page errors ===');
  console.log(errors.length ? errors.join('\n') : '(none)');
  console.log('=== rendered failures ===');
  console.log(failures.length ? failures.join('\n') : '(none)');
  await browser.close();
  if (failures.length && cfg.failOnFailure !== false) process.exit(1);
})().catch((e) => { console.error('FATAL', e); process.exit(1); });
