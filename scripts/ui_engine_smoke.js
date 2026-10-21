/*
 * Other-engine check: every page in a config, rendered by WebKit (Safari's
 * engine) or Gecko (Firefox's), on a phone and on desktop.
 *
 * The rest of the browser CI runs in Chromium, which can't see a script that
 * only breaks in Safari or Firefox (an unsupported API, a parse difference) or
 * a layout that only overflows with another engine's font metrics. This walk
 * fails on exactly those: an uncaught page error, a 5xx, the text a failed
 * render leaves behind, or a page that scrolls sideways.
 *
 * It reads the same target list as the accessibility scan
 * (scripts/ui_a11y_ci.json), so a page added there is checked here too.
 * Expected 4xx pages (a feature off in the seed, /platform for staff) are not
 * failures; a 4xx says nothing about the engine.
 *
 * Usage (app booted and seeded, see the ui-validation skill):
 *   npx playwright install --with-deps webkit firefox
 *   ENGINE=webkit  node scripts/ui_engine_smoke.js scripts/ui_a11y_ci.json
 *   ENGINE=firefox node scripts/ui_engine_smoke.js scripts/ui_a11y_ci.json
 *
 * Env: ENGINE ("webkit" by default), SMOKE_DEVICES (the engine's phone and
 * desktop by default), SMOKE_SCHEMES ("light" by default), SMOKE_OUT
 * (screenshot dir).
 */
const playwright = require('playwright');
const fs = require('fs');
const path = require('path');

// Playwright ships no Android Firefox profile, and Gecko refuses isMobile, so
// this one is a Pixel 7's screen with Firefox for Android's user agent.
const FIREFOX_ANDROID = {
  userAgent: 'Mozilla/5.0 (Android 16; Mobile; rv:144.0) Gecko/144.0 Firefox/144.0',
  viewport: { width: 412, height: 839 },
  deviceScaleFactor: 2.625,
  hasTouch: true,
};

const ENGINES = {
  webkit: { devices: ['iPhone 13', 'Desktop Safari'], extra: {} },
  firefox: { devices: ['Firefox Android', 'Desktop Firefox'], extra: { 'Firefox Android': FIREFOX_ANDROID } },
};

const FAIL_MARKERS = [
  'This section failed to load',
  'Internal Server Error',
  'Traceback (most recent call last)',
];

// Errors raised inside a third-party frame (the Twitch player on /live) reach
// the page's error stream in WebKit; they aren't ours to fix.
const THIRD_PARTY = [/twitch/i, /enumerateDevices/];

const slug = (s) => s.toLowerCase().replace(/[^a-z0-9]+/g, '-');

function profile(engine, name) {
  return ENGINES[engine].extra[name] || playwright.devices[name];
}

async function login(browser, engine, baseUrl, prefix, user) {
  const ctx = await browser.newContext(profile(engine, ENGINES[engine].devices[1]));
  const page = await ctx.newPage();
  await page.goto(`${baseUrl}${prefix}/login`, { waitUntil: 'load' });
  await page.waitForTimeout(1500);
  await page.locator('tr, .wiz-grid-card', { hasText: user }).first()
    .getByRole('button', { name: 'Log in as' }).click();
  await page.waitForTimeout(2000);
  const state = await ctx.storageState();
  await ctx.close();
  return state;
}

async function walk(browser, engine, cfg, deviceName, scheme, storageState, outDir, failures) {
  const baseUrl = cfg.baseUrl || 'http://127.0.0.1:8000';
  const prefix = cfg.tenant ? `/t/${cfg.tenant}` : '';
  const ctx = await browser.newContext({ ...profile(engine, deviceName), colorScheme: scheme, storageState });
  const page = await ctx.newPage();
  let errors = [];
  page.on('pageerror', (e) => {
    const msg = e.message.split('\n')[0].slice(0, 300);
    if (!THIRD_PARTY.some((re) => re.test(msg))) errors.push(msg);
  });
  const dir = path.join(outDir, slug(deviceName), scheme);
  fs.mkdirSync(dir, { recursive: true });

  for (const t of cfg.targets) {
    errors = [];
    const where = `[${deviceName} ${scheme}] ${t.name}`;
    const url = baseUrl + (t.platform || t.path.startsWith('/t/') ? t.path : prefix + t.path);
    let status = null;
    try {
      const resp = await page.goto(url, { waitUntil: 'load', timeout: 30000 });
      status = resp && resp.status();
    } catch (e) {
      failures.push(`${where}: navigation failed: ${e.message.split('\n')[0]}`);
      continue;
    }
    await page.waitForTimeout(2000);
    const found = await page.evaluate((markers) => ({
      sideways: document.documentElement.scrollWidth - window.innerWidth,
      markers: markers.filter((m) => (document.body ? document.body.innerText : '').includes(m)),
    }), FAIL_MARKERS);
    const problems = [];
    if (status && status >= 500) problems.push(`HTTP ${status}`);
    for (const m of found.markers) problems.push(`rendered "${m}"`);
    if (found.sideways > 1) problems.push(`page scrolls sideways by ${found.sideways}px`);
    for (const e of errors) problems.push(`page error: ${e}`);
    await page.screenshot({ path: path.join(dir, `${t.name}.png`) }).catch(() => {});
    if (problems.length) failures.push(`${where}: ${problems.join('; ')}`);
    console.log(`${problems.length ? 'FAIL' : 'ok  '} ${where} (${status})${problems.length ? ' ' + problems.join('; ') : ''}`);
  }
  await ctx.close();
}

(async () => {
  const cfg = JSON.parse(fs.readFileSync(process.argv[2] || 'scripts/ui_a11y_ci.json', 'utf8'));
  const engine = process.env.ENGINE || 'webkit';
  if (!ENGINES[engine]) throw new Error(`unknown ENGINE "${engine}" (webkit or firefox)`);
  const deviceNames = process.env.SMOKE_DEVICES ? process.env.SMOKE_DEVICES.split(',') : ENGINES[engine].devices;
  const schemes = (process.env.SMOKE_SCHEMES || 'light').split(',');
  const outDir = process.env.SMOKE_OUT || path.join(cfg.outDir || '/tmp/ui-smoke', engine);
  for (const d of deviceNames) if (!profile(engine, d)) throw new Error(`unknown device "${d}"`);

  const browser = await playwright[engine].launch();
  const prefix = cfg.tenant ? `/t/${cfg.tenant}` : '';
  const state = await login(browser, engine, cfg.baseUrl || 'http://127.0.0.1:8000', prefix, cfg.loginAs || 'staff_user');
  const failures = [];
  // One context per device and scheme, run side by side: each keeps its own
  // cache, so only the first page of a walk pays for the assets.
  await Promise.all(deviceNames.flatMap((d) => schemes.map(
    (s) => walk(browser, engine, cfg, d, s, state, outDir, failures))));
  await browser.close();

  console.log('=== failures ===');
  console.log(failures.length ? failures.join('\n') : '(none)');
  if (failures.length) process.exit(1);
})().catch((e) => { console.error('FATAL', e); process.exit(1); });
