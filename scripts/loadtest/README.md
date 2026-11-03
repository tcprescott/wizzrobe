# Load-test harness

Reproduces the capacity measurements in
[docs/scaling-roadmap.md](../../docs/scaling-roadmap.md).

`ws_load.py` opens **real NiceGUI websockets** — it performs the same handshake
the bundled `nicegui.js` does (GET the page, scrape the `client_id` that render
minted, then connect socket.io with `implicit_handshake=true`). One virtual
client == one browser tab == one persistent websocket, so the connection count
means the same thing it means in production.

`profile_render.py` cProfiles page renders in-process (via `ASGITransport`, no
network, no new dependencies) to attribute the per-render CPU cost.

## Setup

```bash
bash scripts/setup_env.sh                    # Postgres + dev .env (once)
set -a && . ./.env && set +a
poetry run uvicorn main:app --workers 1 --log-level warning --port 8001 --host 127.0.0.1 &
poetry run python scripts/seed_dev.py
```

Use a **no-reload** server: `./start.sh dev` adds watchfiles overhead, and
`./start.sh prod` forces `ENVIRONMENT=production`, which refuses `MOCK_DISCORD`.

## Getting a session cookie

Login happens over the websocket (the mock picker emits a `login_as` event), so
the cookie has to come from a real browser session. Drive the mock login with
Playwright once, then reuse that cookie for every virtual client — they are
distinct NiceGUI clients regardless of sharing one identity:

```js
const { chromium } = require('playwright');   // NODE_PATH=/opt/node22/lib/node_modules
const b = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium' });
const ctx = await b.newContext(); const p = await ctx.newPage();
await p.goto('http://127.0.0.1:8001/t/default/login');
await p.locator('tr', { hasText: 'staff_user' }).first()
       .locator('button:has-text("Log in as")').click();
console.log((await ctx.cookies()).find(c => c.name === 'session').value);
```

Write it to `cookie.txt` as `session=<value>`.

## Running

```bash
# ramp must exceed the per-render CPU cost or renders queue past NiceGUI's
# 3-second page-response timeout and clients are torn down before connecting
poetry run python scripts/loadtest/ws_load.py \
    --n 500 --ramp 0.3 --hold 60 \
    --base http://127.0.0.1:8001 --prefix /t/default --path /t/default/ \
    --cookie-file cookie.txt

PYTHONPATH=. poetry run python scripts/loadtest/profile_render.py
```

Sample the server alongside a run — RSS, CPU (ticks/s ≈ % of one core), fds, and
`pg_stat_activity` — to separate "CPU-bound" from "waiting on the database".

## Interpreting

- **`Response for / not ready after 3.0 seconds`** in the server log means
  renders are queuing: admission-rate limited, not socket limited.
- **CPU ≈ wall time** over sequential renders means CPU-bound; the DB pool is not
  the constraint (check `pg_active` to confirm).
- Driver and server on one host understate server CPU headroom. Run them apart
  for a clean number.

## Event-size data: how pages, APIs and workers scale

`ws_load.py` asks how many viewers one process holds. The scripts below ask the
other question: how the app behaves once the *data* is event-sized (1,300
matches, 2,000 members) and has a few years of history behind it.

```bash
poetry run python scripts/seed_dev.py
poetry run python scripts/loadtest/seed_scale.py        # ~150s; refuses ENVIRONMENT=production
```

`seed_scale.py` bulk-loads with `COPY` into the `default` community: 2,000
`scale_NNNNN` members (20 staff, 50 proctors, 300 volunteers), 14 tournaments on
4 stages, 1,300 matches from 36 hours ago to 60 hours ahead in the state their
time implies, crew, acks, seeds, comments, reschedule requests, shifts and
assignments, a 2,000-entrant check-in event, equipment loans, community events,
and history: 300k audit rows, 1.5M telemetry events and 100k webhook deliveries.
`--past-years 4` (the default) adds `scale-2025`..`scale-2022` communities, each
with its own finished event and history, so per-community scoping is tested
against rows that are not yours. `--scale 2` doubles everything.

For timings, `pg_stat_statements` must be loaded (`shared_preload_libraries`)
and created in the database.

| Script | Measures |
|---|---|
| `page_timings.js` | Every page in `page_targets.json` in a real browser, until it has settled (lazy tabs and websocket refreshes included): wall time, server CPU, statements and database time, top three statements. `ONLY=<regex>` narrows it. `as` in a target logs in as that user. |
| `measure_backend.py` | In-process, no workers running: every REST `GET`, every read-only MCP tool over the real `/mcp`, and two ticks of each background worker. Time, statements, response size. `--only rest\|mcp\|workers`, `--json out.json`. |
| `trace_queries.py` | Ranks the app call sites behind each statement of one page render (`COOKIE_FILE=`) or one worker tick (`worker:<name>`). An N+1 is one site with a count near the row count. |
| `profile_server.py` | Runs the server under cProfile, dumping on each SIGUSR1. With `PROFILE_PID=<pid>`, `page_timings.js` brackets each page's last load so its profile lands in its own file. |

```bash
./start.sh validate &
NODE_PATH=$(npm root -g) node scripts/loadtest/page_timings.js scripts/loadtest/page_targets.json out.json
PYTHONPATH=. poetry run python scripts/loadtest/measure_backend.py --json backend.json
```

Server CPU is the number to watch. NiceGUI runs every viewer on one event loop,
so a page that spends 1.5s of CPU building itself stalls every other open tab
for that long. asyncpg issues four reset statements per connection checkout,
and running workers add their own, so raw statement counts read high; compare
runs, not absolutes.
