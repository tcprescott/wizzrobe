/*
 * Closed-tab leak check for the running app (MOCK_DISCORD mode).
 *
 * Opens and closes every target page, waits for the server to drop those
 * clients, and reads a memory census from the app (`memory_census.take`, after
 * a full collection). Then does it twice more and compares: a page that holds
 * on to its client makes the count of dead-but-alive pages grow with every
 * round, and the check fails printing what holds them (`stale_clients_held_by`).
 *
 * SGLMAN-29 was this: per-tab state in a WeakKeyDictionary whose value pointed
 * back at its client, so every closed tab's page stayed in memory and every
 * full garbage collection got longer with uptime. Per-tab state must let go in
 * `client.on_delete`; docs/features/observability.md#finding-a-leak-memory_census.
 *
 * A few dead pages held by a shared refreshable's target list are normal (NiceGUI
 * prunes them on its next call), so the check compares rounds rather than
 * expecting zero.
 *
 * The app must run with MEMORY_CENSUS_DUMP=<path>: SIGUSR1 then writes a census
 * to <path>, and the app's pid is in <path>.pid.
 *
 * Usage: MEMORY_CENSUS_DUMP=/tmp/census.json node scripts/ui_leak_check.js scripts/ui_a11y_ci.json
 */
const { chromium } = require('playwright');
const fs = require('fs');
const { execSync } = require('child_process');

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function findChrome(explicit) {
  if (explicit && fs.existsSync(explicit)) return explicit;
  try {
    const hit = execSync('find /opt/pw-browsers -type f -name chrome 2>/dev/null | head -n1').toString().trim();
    if (hit) return hit;
  } catch (_) {}
  return undefined;
}

async function census(dump) {
  const pid = +fs.readFileSync(`${dump}.pid`, 'utf8').trim();
  const before = fs.existsSync(dump) ? fs.statSync(dump).mtimeMs : 0;
  process.kill(pid, 'SIGUSR1');
  for (let i = 0; i < 120; i++) {
    await sleep(500);
    if (fs.existsSync(dump) && fs.statSync(dump).mtimeMs !== before) {
      return JSON.parse(fs.readFileSync(dump, 'utf8'));
    }
  }
  throw new Error('the app wrote no census within 60s');
}

// Closed tabs stay registered until NiceGUI's reconnect window runs out
// (frontend.py: 30s), so wait for the server to drop them before counting.
async function settled(dump) {
  let last;
  for (let i = 0; i < 24; i++) {
    last = await census(dump);
    if (last.clients_registered === 0) return last;
    await sleep(5000);
  }
  throw new Error(`${last.clients_registered} pages still registered 2 minutes after every tab closed`);
}

const stale = (c) => c.clients_alive - c.clients_registered;

(async () => {
  const cfg = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
  const dump = process.env.MEMORY_CENSUS_DUMP;
  if (!dump) throw new Error('set MEMORY_CENSUS_DUMP to the path the app was started with');
  const baseUrl = cfg.baseUrl || 'http://127.0.0.1:8000';
  const loginAs = cfg.loginAs || 'staff_user';
  const tprefix = cfg.tenant ? `/t/${cfg.tenant}` : '';
  const withTenant = (p, t) => (!tprefix || (t && t.platform) || p.startsWith('/t/') ? p : tprefix + p);
  const targets = cfg.targets || [];
  const concurrency = 6;

  const browser = await chromium.launch({ executablePath: findChrome(cfg.chrome), args: ['--no-sandbox'] });
  const loginCtx = await browser.newContext();
  const loginPage = await loginCtx.newPage();
  await loginPage.goto(`${baseUrl}${withTenant('/login')}`, { waitUntil: 'networkidle' });
  await loginPage.waitForTimeout(800);
  await loginPage.locator('tr, .wiz-grid-card', { hasText: loginAs }).first().getByRole('button', { name: 'Log in as' }).click();
  await loginPage.waitForTimeout(1500);
  const storageState = await loginCtx.storageState();
  await loginCtx.close();
  const ctx = await browser.newContext({ storageState, viewport: { width: 1440, height: 900 } });

  async function visitAll() {
    const queue = [...targets];
    await Promise.all(Array.from({ length: concurrency }, async () => {
      for (let t = queue.shift(); t; t = queue.shift()) {
        const page = await ctx.newPage();
        try {
          await page.goto(`${baseUrl}${withTenant(t.path, t)}`, { waitUntil: 'networkidle', timeout: 30000 });
          await page.waitForTimeout(1000);
        } catch (e) {
          console.log(`  ${t.name}: ${e.message.split('\n')[0]}`);
        }
        await page.close();
      }
    }));
  }

  console.log(`round 1: ${targets.length} pages`);
  await visitAll();
  const first = await settled(dump);
  console.log(`  dead pages still in memory: ${stale(first)}, live-board subscribers: ${first.match_live_subscribers}`);

  console.log('rounds 2 and 3');
  await visitAll();
  await visitAll();
  const last = await settled(dump);
  console.log(`  dead pages still in memory: ${stale(last)}, live-board subscribers: ${last.match_live_subscribers}`);
  await browser.close();

  const grew = stale(last) > stale(first) || last.match_live_subscribers > first.match_live_subscribers;
  if (grew) {
    console.log('\nClosed tabs are leaking: the count grew with every round of the same pages.');
    console.log('What holds them (each chain reads: object <- what refers to it <- ...):');
    for (const h of last.stale_clients_held_by) console.log(`  ${h.count}x ${h.chain}`);
    console.log('\nDeleted elements, the same way:');
    for (const h of last.deleted_elements_held_by) console.log(`  ${h.count}x ${h.chain}`);
    console.log('\nRelease per-tab state in client.on_delete (see docs/features/observability.md, "Finding a leak").');
    process.exit(1);
  }
  console.log('ok: closed tabs are let go');
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
