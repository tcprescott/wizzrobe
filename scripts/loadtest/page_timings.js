/*
 * Time every page against a scaled dev database, with the database cost of each.
 *
 * For each target: reset pg_stat_statements, open the page, wait until it has
 * *settled* (no request in flight and no DOM change for SETTLE_MS — lazy tabs and
 * websocket-pushed refreshes count, which a plain `networkidle` misses), then read
 * back how many statements ran and how long Postgres spent on them. Two loads per
 * target; the report keeps the faster, so a cold cache on first touch does not
 * read as a slow page.
 *
 * Prereqs: seed_dev.py then seed_scale.py, `./start.sh validate`, and
 * pg_stat_statements loaded (shared_preload_libraries) and created in the DB.
 *
 *   NODE_PATH=$(npm root -g) node scripts/loadtest/page_timings.js scripts/loadtest/page_targets.json out.json
 *
 * The config's `as` picks the mock-login identity per target, so a player's My
 * Schedule is timed as a player with a full schedule rather than as staff.
 * PGURL (default postgres://postgres:devpass@localhost/wizzrobe) names the DB.
 */
const { chromium } = require('playwright');
const fs = require('fs');
const { execFileSync } = require('child_process');

const SETTLE_MS = 750;
const PROFILE_PID = process.env.PROFILE_PID;
let profileN = 0;
const CAP_MS = 45000;
const PGURL = process.env.PGURL || 'postgres://postgres:devpass@localhost/wizzrobe';

function psql(sql) {
  return execFileSync('psql', [PGURL, '-Atc', sql], { encoding: 'utf8' }).trim();
}

// The server's CPU time, so a page's cost is visible apart from network and
// browser time. SERVER_PID overrides discovery.
const SERVER_PID = process.env.SERVER_PID || execFileSync('pgrep', ['-f', 'uvicorn main:app'], { encoding: 'utf8' })
  .trim().split('\n').pop();
function cpuMs() {
  const f = fs.readFileSync(`/proc/${SERVER_PID}/stat`, 'utf8').split(') ')[1].split(' ');
  return (Number(f[11]) + Number(f[12])) * 10;
}

function resetStats() { psql('SELECT pg_stat_statements_reset()'); }

function readStats() {
  const where = "query NOT ILIKE '%pg_stat_statements%' AND dbid = (SELECT oid FROM pg_database WHERE datname = current_database())";
  const [calls, ms] = psql(`SELECT coalesce(sum(calls),0), round(coalesce(sum(total_exec_time),0)::numeric,1) FROM pg_stat_statements WHERE ${where}`).split('|');
  const top = psql(`SELECT calls || '|' || round(total_exec_time::numeric,1) || '|' || regexp_replace(left(query, 160), '\\s+', ' ', 'g') FROM pg_stat_statements WHERE ${where} ORDER BY total_exec_time DESC LIMIT 3`)
    .split('\n').filter(Boolean).map(l => { const [c, t, ...q] = l.split('|'); return { calls: +c, ms: +t, query: q.join('|') }; });
  return { queries: +calls, db_ms: +ms, top };
}

async function login(browser, base, tenant, username) {
  const ctx = await browser.newContext({ viewport: { width: 1500, height: 1000 } });
  const page = await ctx.newPage();
  await page.goto(`${base}/t/${tenant}/login`, { waitUntil: 'networkidle' });
  const filter = page.getByLabel('Filter by username or discord_id');
  if (await filter.count()) { await filter.fill(username); await page.waitForTimeout(600); }
  const row = page.locator('tr, .wiz-grid-card', { hasText: username }).first();
  await row.getByRole('button', { name: 'Log in as' }).click();
  await page.waitForTimeout(1500);
  return { ctx, page };
}

async function settle(page) {
  await page.evaluate(() => {
    window.__wizLast = performance.now();
    new MutationObserver(() => { window.__wizLast = performance.now(); })
      .observe(document, { subtree: true, childList: true, attributes: true, characterData: true });
  });
  let inflight = 0;
  const on = () => { inflight += 1; };
  const off = () => { inflight = Math.max(0, inflight - 1); };
  page.on('request', on); page.on('requestfinished', off); page.on('requestfailed', off);
  const t0 = Date.now();
  try {
    for (;;) {
      await page.waitForTimeout(100);
      const quiet = await page.evaluate(() => performance.now() - window.__wizLast);
      if (inflight === 0 && quiet >= 750) return Date.now() - t0 - 750;
      if (Date.now() - t0 > 45000) return -1;
    }
  } finally {
    page.off('request', on); page.off('requestfinished', off); page.off('requestfailed', off);
  }
}

async function timeOnce(page, url, target) {
  resetStats();
  const c0 = cpuMs();
  const t0 = Date.now();
  const resp = await page.goto(url, { waitUntil: 'domcontentloaded', timeout: CAP_MS });
  const doc_ms = Date.now() - t0;
  if (target.click) {
    await page.locator(target.click).first().click({ timeout: 10000 }).catch(() => {});
  }
  const settled = await settle(page);
  const total_ms = settled < 0 ? -1 : Date.now() - t0 - SETTLE_MS;
  const cpu_ms = cpuMs() - c0;
  const stats = readStats();
  const body = await page.evaluate(() => document.body ? document.body.innerText : '');
  const failed = ['This section failed to load', 'Internal Server Error', 'Traceback'].find(m => body.includes(m));
  return { status: resp ? resp.status() : 0, doc_ms, total_ms, cpu_ms, ...stats, failed: failed || null };
}

(async () => {
  const cfg = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
  const out = process.argv[3] || '/tmp/page_timings.json';
  const base = cfg.baseUrl || 'http://127.0.0.1:8000';
  const tenant = cfg.tenant || 'default';
  const only = process.env.ONLY ? new RegExp(process.env.ONLY) : null;
  const browser = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium', args: ['--no-sandbox'] })
    .catch(() => chromium.launch({ args: ['--no-sandbox'] }));
  const sessions = {};
  const results = [];
  for (const t of cfg.targets) {
    if (only && !only.test(t.name)) continue;
    const who = t.as || cfg.loginAs || 'staff_user';
    if (!sessions[who]) sessions[who] = await login(browser, base, tenant, who);
    const { page } = sessions[who];
    const url = t.path.startsWith('/t/') || t.platform ? base + t.path : `${base}/t/${tenant}${t.path}`;
    const runs = [];
    for (let i = 0; i < (cfg.repeat || 2); i++) {
      // Under profile_server.py, bracket the last run with SIGUSR1 so its
      // profile lands in a file of its own (named in the log line).
      const profiling = PROFILE_PID && i === (cfg.repeat || 2) - 1;
      if (profiling) { process.kill(+PROFILE_PID, 'SIGUSR1'); profileN += 1; await page.waitForTimeout(300); }
      try { runs.push(await timeOnce(page, url, t)); } catch (e) { runs.push({ error: e.message.split('\n')[0] }); }
      if (profiling) { process.kill(+PROFILE_PID, 'SIGUSR1'); profileN += 1; console.log(`  profile: ${profileN}.prof`); }
    }
    const ok = runs.filter(r => !r.error && r.total_ms >= 0);
    const best = ok.length ? ok.reduce((a, b) => (a.total_ms <= b.total_ms ? a : b)) : runs[runs.length - 1];
    const row = { name: t.name, path: t.path, as: who, ...best, runs: runs.map(r => r.total_ms ?? r.error) };
    results.push(row);
    console.log(`${t.name.padEnd(32)} ${String(row.total_ms).padStart(6)}ms total  ${String(row.doc_ms).padStart(6)}ms doc  ${String(row.cpu_ms).padStart(6)}ms cpu  ` +
      `${String(row.queries).padStart(5)} q  ${String(row.db_ms).padStart(7)}ms db  ${row.failed || row.error || ''}`);
  }
  fs.writeFileSync(out, JSON.stringify(results, null, 2));
  await browser.close();
})().catch(e => { console.error('FATAL', e); process.exit(1); });
