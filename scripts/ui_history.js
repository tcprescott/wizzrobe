/*
 * Back, Forward, reload and deep links on in-page view switchers.
 *
 * Every switch that redraws part of a page without loading a new one (a hub
 * tab, My Schedule's List / Timeline, the Event board's views, a timeline day)
 * must move the address bar and the browser tab's title with it, so Back undoes
 * it, Forward redoes it, a reload stays put and a copied link opens the same
 * view (theme/url_state.py). This walks scripted scenarios in each browser and
 * viewport and fails on the first expectation that does not hold.
 *
 * Usage:
 *   NODE_PATH=$(npm root -g) node scripts/ui_history.js scripts/ui_history_ci.json
 *   HISTORY_BROWSERS=chromium,webkit   which engines to run (default: config's, else chromium)
 *   HISTORY_ONLY=<scenario name>       run one scenario
 *
 * A scenario is a list of steps, each one of:
 *   { "goto": "/home/my-schedule" }          load a page (tenant-prefixed)
 *   { "click": "Timeline" }                  click the button / tab / item with that text
 *   { "click": "Timeline", "within": ".css" }  ...inside the first match of a selector
 *   { "clickSelector": ".css" }              click the first visible match of a selector
 *   { "back": true } / { "forward": true } / { "reload": true }
 *   { "expect": { "url": "regex", "noUrl": "regex", "title": "regex",
 *                 "pressed": "text", "visible": "css", "hidden": "css",
 *                 "inView": "css" } }
 * `inView` waits for a #fragment link to have scrolled its target on screen.
 * `url` matches the path + query after the tenant prefix. `pressed` is the
 * label of a segmented-switch button that must be the selected one.
 */
const playwright = require('playwright');
const fs = require('fs');
const path = require('path');
const { execSync } = require('child_process');

function findChrome(explicit) {
  if (explicit && fs.existsSync(explicit)) return explicit;
  try {
    const hit = execSync("find /opt/pw-browsers -type f -name chrome 2>/dev/null | head -n1").toString().trim();
    if (hit) return hit;
  } catch (_) {}
  return undefined;
}

function profiles(list) {
  return (list || [{ name: 'desktop', viewport: { width: 1440, height: 900 } }, 'iPhone 13']).map((d) => {
    if (typeof d !== 'string') return d;
    const { defaultBrowserType, ...profile } = playwright.devices[d];
    return { name: d, ...profile };
  });
}

async function settle(page) {
  await page.waitForLoadState('networkidle').catch(() => {});
  await page.waitForTimeout(700);
}

async function clickText(page, text, within, mobile) {
  const scope = within ? page.locator(within).first() : page;
  const candidates = [
    scope.locator('.q-btn-toggle button', { hasText: text }),
    scope.locator('.wiz-bottom-nav [role="tab"]', { hasText: text }),
    scope.getByRole('tab', { name: text, exact: true }),
    scope.getByRole('button', { name: text, exact: true }),
  ];
  for (const loc of candidates) {
    const el = loc.first();
    if (await el.count() && await el.isVisible().catch(() => false)) {
      await el.click();
      return;
    }
  }
  // A hub tab that is only in the drawer: open the drawer first on a phone.
  if (mobile) {
    const burger = page.locator('.wiz-header button[aria-label*="enu"], .wiz-header .q-btn').first();
    if (await burger.count()) {
      await burger.click();
      await page.waitForTimeout(400);
    }
  }
  const item = page.locator('.q-drawer .q-item', { hasText: text }).first();
  if (await item.count()) {
    await item.click();
    return;
  }
  throw new Error(`nothing to click with text "${text}"`);
}

async function check(page, want, prefix) {
  const problems = [];
  const deadline = Date.now() + 6000;
  for (;;) {
    problems.length = 0;
    const url = new URL(page.url());
    const local = (url.pathname.startsWith(prefix) ? url.pathname.slice(prefix.length) : url.pathname) + url.search;
    if (want.url && !new RegExp(want.url).test(local)) problems.push(`url ${local} !~ ${want.url}`);
    if (want.noUrl && new RegExp(want.noUrl).test(local)) problems.push(`url ${local} should not match ${want.noUrl}`);
    const title = await page.title();
    if (want.title && !new RegExp(want.title).test(title)) problems.push(`title "${title}" !~ ${want.title}`);
    if (want.pressed) {
      const pressed = await page.locator('.q-btn-toggle button[aria-pressed="true"]:visible').allInnerTexts();
      if (!pressed.some((t) => t.trim().startsWith(want.pressed))) {
        problems.push(`"${want.pressed}" not selected (selected: ${JSON.stringify(pressed.map((t) => t.trim()))})`);
      }
    }
    if (want.visible && !(await page.locator(want.visible).first().isVisible().catch(() => false))) {
      problems.push(`${want.visible} not visible`);
    }
    if (want.hidden && await page.locator(want.hidden).first().isVisible().catch(() => false)) {
      problems.push(`${want.hidden} still visible`);
    }
    if (want.inView) {
      const box = await page.locator(want.inView).first().boundingBox().catch(() => null);
      const vh = page.viewportSize().height;
      if (!box || box.y < 0 || box.y > vh * 0.6) problems.push(`${want.inView} not scrolled into view (y=${box && Math.round(box.y)})`);
    }
    if (!problems.length || Date.now() > deadline) break;
    await page.waitForTimeout(250);
  }
  return problems;
}

(async () => {
  const cfg = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
  const baseUrl = cfg.baseUrl || 'http://127.0.0.1:8000';
  const prefix = cfg.tenant ? `/t/${cfg.tenant}` : '';
  const withTenant = (p) => (!prefix || p.startsWith('/t/') ? p : prefix + p);
  const outDir = cfg.outDir || '/tmp/ui-history';
  fs.mkdirSync(outDir, { recursive: true });
  const engines = (process.env.HISTORY_BROWSERS || (cfg.browsers || ['chromium']).join(',')).split(',');
  const scenarios = (cfg.scenarios || []).filter((s) => !process.env.HISTORY_ONLY || s.name === process.env.HISTORY_ONLY);
  const failures = [];

  for (const engine of engines) {
    const type = playwright[engine];
    const browser = await type.launch(engine === 'chromium'
      ? { executablePath: findChrome(cfg.chrome), args: ['--no-sandbox'] } : {});
    const loginCtx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    const loginPage = await loginCtx.newPage();
    await loginPage.goto(`${baseUrl}${withTenant('/login')}`, { waitUntil: 'networkidle' });
    await loginPage.waitForTimeout(800);
    await loginPage.locator('tr, .wiz-grid-card', { hasText: cfg.loginAs || 'staff_user' }).first()
      .getByRole('button', { name: 'Log in as' }).click();
    await loginPage.waitForTimeout(1500);
    const storageState = await loginCtx.storageState();
    await loginCtx.close();

    for (const vp of profiles(cfg.viewports)) {
      // WebKit has no isMobile emulation on Linux; the viewport is what matters here.
      const { isMobile, ...vpProfile } = vp;
      const ctxOpts = engine === 'firefox' || engine === 'webkit' ? vpProfile : vp;
      for (const sc of scenarios) {
        if (sc.viewports && !sc.viewports.includes(vp.name)) continue;
        const label = `${engine} ${vp.name} ${sc.name}`;
        const ctx = await browser.newContext({ ...ctxOpts, storageState });
        let page = await ctx.newPage();
        const errors = [];
        page.on('pageerror', (e) => errors.push(String(e)));
        let n = 0;
        try {
          for (const step of sc.steps) {
            n += 1;
            if (step.goto) {
              await page.goto(`${baseUrl}${withTenant(step.goto)}`, { waitUntil: 'networkidle' });
              await settle(page);
            } else if (step.clickSelector) {
              await page.locator(`${step.clickSelector} >> visible=true`).first().click();
              await settle(page);
            } else if (step.click) {
              await clickText(page, step.click, step.within, !!vp.isMobile || (vp.viewport && vp.viewport.width < 1024));
              await settle(page);
            } else if (step.back) {
              await page.goBack({ waitUntil: 'commit' });
              await settle(page);
            } else if (step.forward) {
              await page.goForward({ waitUntil: 'commit' });
              await settle(page);
            } else if (step.reload) {
              // Playwright's WebKit on Linux crashes any navigation of a tab
              // that has done a same-document Back/Forward (even on a static
              // file), so WebKit opens the address in a fresh tab instead: the
              // same request a reload makes.
              if (engine === 'webkit') {
                const url = page.url();
                const fresh = await ctx.newPage();
                fresh.on('pageerror', (e) => errors.push(String(e)));
                await page.close();
                page = fresh;
                await page.goto(url, { waitUntil: 'networkidle' });
              } else {
                await page.reload({ waitUntil: 'networkidle' });
              }
              await settle(page);
            }
            if (step.expect) {
              const problems = await check(page, step.expect, prefix);
              if (problems.length) throw new Error(`step ${n}: ${problems.join('; ')}`);
            }
          }
          if (errors.length) throw new Error(`page errors: ${errors.join(' | ')}`);
          console.log(`ok   ${label}`);
        } catch (e) {
          const shot = path.join(outDir, `${label.replace(/[^a-z0-9]+/gi, '-')}.png`);
          await page.screenshot({ path: shot, fullPage: false }).catch(() => {});
          failures.push(`${label}: ${e.message}`);
          console.log(`FAIL ${label}: ${e.message}`);
        }
        await ctx.close();
      }
    }
    await browser.close();
  }
  if (failures.length) {
    console.log(`\n=== ${failures.length} failure(s) ===\n${failures.join('\n')}`);
    process.exit(1);
  }
  console.log('\nAll history scenarios passed.');
})();
