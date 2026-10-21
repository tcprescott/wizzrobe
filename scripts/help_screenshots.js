/*
 * Regenerate the screenshots the /help articles show.
 *
 * Reads scripts/help_screenshots/<slug>.json (one file per article, so help PRs
 * for different articles never edit the same manifest), signs in to the running
 * dev app as each entry's seeded user, opens the page, runs any setup steps, crops to the
 * element the shot is about, and writes static/help/<slug>/<name>.webp. Re-run
 * it after a UI change and every screenshot that shows the change is current
 * again; `git diff --stat static/help` shows which ones moved.
 *
 * Prereqs are the /ui-validation loop's: `bash scripts/setup_env.sh`, boot with
 * `./start.sh validate`, seed with `poetry run python scripts/seed_dev.py`.
 *
 * Usage:
 *   NODE_PATH=$(npm root -g) node scripts/help_screenshots.js            # every shot
 *   NODE_PATH=$(npm root -g) node scripts/help_screenshots.js player     # one article
 *   NODE_PATH=$(npm root -g) node scripts/help_screenshots.js player/schedule-phone
 *   ... --base-url http://127.0.0.1:8000
 *
 * Each file is a JSON array of entries; the file name is the article slug and
 * the output directory, static/help/<slug>/. An entry (only name and path are
 * required):
 *   {
 *     "name": "schedule-phone",      // output file <name>.webp
 *     "user": "player_one",          // seeded mock-login username; null = signed out
 *     "tenant": "default",           // /t/<slug> prefix; "" for the bare platform host
 *     "path": "/home/schedule",      // tenant-relative page to open
 *     "viewport": "phone",           // "phone" (390x844 @2x) | "desktop" (1280x800) | {width,height}
 *     "selector": ".wiz-panel",      // crop to this element (first match); omit for the viewport
 *     "padding": 8,                  // px of page around the cropped element
 *     "fullPage": false,             // whole scrollable page instead of the viewport (no selector)
 *     "hide": [".some-banner"],      // hidden before capture, on top of the defaults
 *     "remove": [".some-banner"],    // taken out of the layout (display: none) so nothing gapes
 *     "annotate": [                  // numbered red call-outs drawn over controls
 *       { "selector": "text=Sign up", "n": 1 },   // optional "tab": "top" | "bottom"
 *     ],
 *     "setup": [                     // run in order after the page settles
 *       { "click": "text=Request a match" },
 *       { "fill": "input[aria-label=Search]", "value": "zelda" },
 *       { "press": "Escape" },
 *       { "waitFor": ".q-dialog" },
 *       { "wait": 500 }
 *     ]
 *   }
 * DEFAULTS below supplies any field an entry leaves out.
 *
 * Shots are taken in the light theme with reduced motion. WebP is encoded by
 * Chromium itself (canvas.toDataURL), so this needs nothing beyond the global
 * Playwright the /ui-validation harness already uses.
 */
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');
const { execSync } = require('child_process');

const ROOT = path.resolve(__dirname, '..');
const MANIFEST_DIR = path.join(__dirname, 'help_screenshots');
const OUT_ROOT = path.join(ROOT, 'static', 'help');

const DEFAULTS = { tenant: 'default', viewport: 'phone', padding: 8 };

const VIEWPORTS = {
  phone: { viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true },
  desktop: { viewport: { width: 1280, height: 800 }, deviceScaleFactor: 1, isMobile: false, hasTouch: false },
};

// Transient chrome that would date a screenshot or cover what it is about.
const DEFAULT_HIDE = ['.q-notifications', '.q-loading-bar', '.wiz-skip-link'];

const WEBP_QUALITY = 0.85;

function findChrome() {
  try {
    const hit = execSync('find /opt/pw-browsers -type f -name chrome 2>/dev/null | head -n1').toString().trim();
    if (hit) return hit;
  } catch (_) {}
  return undefined;
}

function loadManifest() {
  return fs.readdirSync(MANIFEST_DIR)
    .filter((f) => f.endsWith('.json'))
    .sort()
    .flatMap((f) => {
      const slug = path.basename(f, '.json');
      const entries = JSON.parse(fs.readFileSync(path.join(MANIFEST_DIR, f), 'utf8'));
      return entries.map((s) => ({ ...DEFAULTS, ...s, slug }));
    });
}

function parseArgs(argv) {
  const args = { only: null, baseUrl: 'http://127.0.0.1:8000' };
  for (let i = 0; i < argv.length; i++) {
    if (argv[i] === '--base-url') args.baseUrl = argv[++i];
    else args.only = argv[i];
  }
  return args;
}

function viewportFor(spec) {
  if (spec && typeof spec === 'object') {
    return { viewport: spec, deviceScaleFactor: 1, isMobile: false, hasTouch: false };
  }
  const preset = VIEWPORTS[spec || 'phone'];
  if (!preset) throw new Error(`unknown viewport "${spec}"`);
  return preset;
}

function tenantPath(tenant, p) {
  if (!tenant || p.startsWith('/t/')) return p;
  return `/t/${tenant}${p}`;
}

async function settle(page) {
  await page.waitForLoadState('networkidle').catch(() => {});
  // NiceGUI builds the page over its websocket after load; give it a beat.
  await page.waitForTimeout(1200);
}

async function signIn(context, baseUrl, tenant, user) {
  const page = await context.newPage();
  await page.goto(`${baseUrl}${tenantPath(tenant, '/login')}`, { waitUntil: 'networkidle' });
  await page.waitForTimeout(800);
  // The mock picker is a table on desktop and .wiz-grid-card cards on a phone.
  const row = page.locator('tr, .wiz-grid-card', { hasText: user }).first();
  await row.getByRole('button', { name: 'Log in as' }).click();
  await page.waitForTimeout(1500);
  await page.close();
}

async function runSetup(page, steps) {
  for (const step of steps || []) {
    if (step.click) await page.locator(step.click).first().click();
    else if (step.fill) await page.locator(step.fill).first().fill(step.value ?? '');
    else if (step.press) await page.keyboard.press(step.press);
    else if (step.waitFor) await page.locator(step.waitFor).first().waitFor({ state: 'visible' });
    else if (step.wait) await page.waitForTimeout(step.wait);
    else throw new Error(`unknown setup step ${JSON.stringify(step)}`);
    if (!step.wait) await page.waitForTimeout(400);
  }
}

// Numbered call-outs: a red outline padded clear of the control, with its number
// in a tab on the outline's top-left edge (or bottom-left when the top would fall
// outside the capture). Drawn as page overlays so they scale with the device.
// They hang off <html>, not <body>: with a dialog open Quasar pins the body with
// position:fixed and a negative top, which would shift anything placed inside it.
const MARK = { color: '#D92D20', border: 3, radius: 6, gap: 4, tab: 21 };

async function drawAnnotations(page, marks, captureTop) {
  if (!marks || !marks.length) return [];
  const boxes = [];
  for (const mark of marks) {
    const target = page.locator(mark.selector).first();
    await target.waitFor({ state: 'visible' });
    const r = await target.evaluate((el) => {
      const b = el.getBoundingClientRect();
      return { x: b.left + window.scrollX, y: b.top + window.scrollY, width: b.width, height: b.height };
    });
    boxes.push({ ...r, n: String(mark.n), tab: mark.tab });
  }
  return page.evaluate(({ boxes, M, captureTop }) => {
    const extents = [];
    for (const b of boxes) {
      const left = b.x - M.gap;
      const top = b.y - M.gap;
      const width = b.width + M.gap * 2;
      const height = b.height + M.gap * 2;
      const box = document.createElement('div');
      box.className = 'wiz-help-annotation';
      Object.assign(box.style, {
        position: 'absolute', left: `${left}px`, top: `${top}px`,
        width: `${width}px`, height: `${height}px`, boxSizing: 'border-box',
        border: `${M.border}px solid ${M.color}`, borderRadius: `${M.radius}px`,
        pointerEvents: 'none', zIndex: '2147483646', margin: '0',
      });
      const onTop = b.tab === 'bottom' ? false : (b.tab === 'top' || top - M.tab >= captureTop);
      const tab = document.createElement('div');
      tab.className = 'wiz-help-annotation';
      tab.textContent = b.n;
      Object.assign(tab.style, {
        position: 'absolute', left: `${left}px`,
        top: onTop ? `${top - M.tab}px` : `${top + height}px`,
        height: `${M.tab}px`, minWidth: '22px', padding: '0 4px', boxSizing: 'border-box',
        background: M.color, color: '#fff', font: 'bold 12px/21px system-ui, sans-serif',
        textAlign: 'center', borderRadius: onTop ? `${M.radius}px ${M.radius}px 0 0` : `0 0 ${M.radius}px ${M.radius}px`,
        pointerEvents: 'none', zIndex: '2147483647', margin: '0', letterSpacing: '0',
      });
      document.documentElement.appendChild(box);
      document.documentElement.appendChild(tab);
      extents.push({
        x1: left, x2: left + width,
        y1: onTop ? top - M.tab : top, y2: onTop ? top + height : top + height + M.tab,
      });
    }
    return extents;
  }, { boxes, M: MARK, captureTop });
}

async function clearAnnotations(page) {
  await page.evaluate(() => document.querySelectorAll('.wiz-help-annotation').forEach((el) => el.remove()));
}

async function capturePng(page, shot) {
  const options = { animations: 'disabled', caret: 'hide', type: 'png' };
  try {
    if (!shot.selector) {
      const top = shot.fullPage ? 0 : await page.evaluate(() => window.scrollY);
      await drawAnnotations(page, shot.annotate, top);
      return await page.screenshot({ ...options, fullPage: Boolean(shot.fullPage) });
    }
    const target = page.locator(shot.selector).first();
    await target.waitFor({ state: 'visible' });
    await target.scrollIntoViewIfNeeded();
    // Page coordinates rather than viewport ones, so an element taller than the
    // viewport is captured whole.
    const box = await target.evaluate((el) => {
      const r = el.getBoundingClientRect();
      return { x: r.left + window.scrollX, y: r.top + window.scrollY, width: r.width, height: r.height };
    });
    const pad = shot.padding ?? 8;
    const size = await page.evaluate(() => ({
      width: document.documentElement.scrollWidth, height: document.documentElement.scrollHeight,
    }));
    const x = Math.max(0, box.x - pad);
    const y = Math.max(0, box.y - pad);
    const clip = {
      x, y,
      width: Math.min(size.width - x, box.width + pad * 2),
      height: Math.min(size.height - y, box.height + pad * 2),
    };
    // A mark on a control at the crop's edge widens the crop just enough to
    // show the whole outline, rather than cutting it off.
    for (const e of await drawAnnotations(page, shot.annotate, y)) {
      const x1 = Math.max(0, Math.min(clip.x, e.x1 - 1));
      const x2 = Math.min(size.width, Math.max(clip.x + clip.width, e.x2 + 1));
      const y2 = Math.min(size.height, Math.max(clip.y + clip.height, e.y2 + 1));
      clip.width = x2 - x1; clip.x = x1; clip.height = y2 - clip.y;
    }
    return await page.screenshot({ ...options, fullPage: true, clip });
  } finally {
    if (shot.annotate && shot.annotate.length) await clearAnnotations(page).catch(() => {});
  }
}

async function toWebp(encoder, png) {
  const dataUrl = await encoder.evaluate(async ({ b64, quality }) => {
    const img = new Image();
    img.src = `data:image/png;base64,${b64}`;
    await img.decode();
    const canvas = document.createElement('canvas');
    canvas.width = img.naturalWidth;
    canvas.height = img.naturalHeight;
    canvas.getContext('2d').drawImage(img, 0, 0);
    return canvas.toDataURL('image/webp', quality);
  }, { b64: png.toString('base64'), quality: WEBP_QUALITY });
  if (!dataUrl.startsWith('data:image/webp')) throw new Error('this Chromium cannot encode WebP');
  return Buffer.from(dataUrl.split(',')[1], 'base64');
}

(async () => {
  const args = parseArgs(process.argv.slice(2));
  const shots = loadManifest()
    .filter((s) => !args.only || args.only === s.slug || args.only === `${s.slug}/${s.name}`);
  if (!shots.length) {
    console.error(`no manifest entries match "${args.only}"`);
    process.exit(1);
  }

  const browser = await chromium.launch({ executablePath: findChrome(), args: ['--no-sandbox'] });
  const encoder = await (await browser.newContext()).newPage();
  const contexts = new Map();
  const failures = [];

  async function contextFor(shot) {
    const view = typeof shot.viewport === 'object' ? JSON.stringify(shot.viewport) : (shot.viewport || 'phone');
    const key = `${shot.user || ''}|${shot.tenant || ''}|${view}`;
    if (!contexts.has(key)) {
      const context = await browser.newContext({
        ...viewportFor(shot.viewport),
        colorScheme: 'light',
        reducedMotion: 'reduce',
        timezoneId: shot.timezone || 'America/New_York',
        locale: 'en-US',
      });
      if (shot.user) await signIn(context, args.baseUrl, shot.tenant, shot.user);
      contexts.set(key, context);
    }
    return contexts.get(key);
  }

  for (const shot of shots) {
    const label = `${shot.slug}/${shot.name}`;
    let page;
    try {
      page = await (await contextFor(shot)).newPage();
      const resp = await page.goto(`${args.baseUrl}${tenantPath(shot.tenant, shot.path)}`, { waitUntil: 'networkidle' });
      if (resp && resp.status() >= 400) throw new Error(`HTTP ${resp.status()} on ${shot.path}`);
      await settle(page);
      await runSetup(page, shot.setup);
      const hide = DEFAULT_HIDE.concat(shot.hide || []);
      await page.addStyleTag({ content: `${hide.join(', ')} { visibility: hidden !important; }` });
      if (shot.remove && shot.remove.length) {
        await page.addStyleTag({ content: `${shot.remove.join(', ')} { display: none !important; }` });
        await page.waitForTimeout(300);
      }
      const png = await capturePng(page, shot);
      const webp = await toWebp(encoder, png);
      const out = path.join(OUT_ROOT, shot.slug, `${shot.name}.webp`);
      fs.mkdirSync(path.dirname(out), { recursive: true });
      fs.writeFileSync(out, webp);
      console.log(`ok   ${label}  ${(webp.length / 1024).toFixed(0)} KB  ${path.relative(ROOT, out)}`);
    } catch (e) {
      failures.push(label);
      console.log(`FAIL ${label}  ${e.message.split('\n')[0]}`);
    } finally {
      if (page) await page.close();
    }
  }

  await browser.close();
  if (failures.length) {
    console.log(`\n${failures.length} shot(s) failed: ${failures.join(', ')}`);
    process.exit(1);
  }
})().catch((e) => { console.error('FATAL', e); process.exit(1); });
