# Scaling roadmap

**Status: the 500-concurrent-websocket target is met on one worker.** This is the
remaining-work record, not a call to action — Phase 1 shipped and nothing below is
currently needed. It exists so the next capacity push starts from measurements
rather than guesses.

## What is already true

After lazy tab rendering shipped, the load harness went from *205/500 connected,
169 still up after a 60 s hold* to **500/500 connected, 500 still up**. Render CPU:
tenant home 289 ms → 132 ms, admin 566 ms → 116 ms. A follow-up fix (the match
table was loading three times per render) took the home page to **74 ms CPU and 26
queries**, down from 377 queries.

**The bottleneck is admission, not holding.** Holding sockets is cheap — 2–4 % of a
core for ~195 sockets, ~7 % for 500. Admitting them is not: 20 sequential renders
measured 5.22 s wall / 4.93 s CPU, **94 % CPU-bound**. The database was never
contended (6 connections used throughout, `pg_active` never above 1) — raising the
pool cap buys nothing.

Measured admission ceiling is **~10 tabs/second**, with the cliff between 10 and 16:
500 tabs connect cleanly at 3.3/s, 6.7/s and 10/s; at 16/s only 92/500 connect with
p50 render latency of 4 s. Reproduce with `scripts/loadtest/`.

**Spectators need not be tabs at all.** The cheapest socket is the one never
opened: the bracket views have static, server-rendered, cached twins under
`/live/…` (no NiceGUI, no socket, ~1 request per reader per minute) — see
[brackets.md](features/brackets.md#static-spectator-views--the-link-you-send-a-stream).
Where a read-only surface is what a crowd actually wants, that pattern moves the
whole crowd out of the admission budget above.

Sizing: **~0.85 MB RSS per connected tab** plus a ~145 MB baseline, so 500 tabs ≈
560 MB. Replica count sizes from the arrival burst, not the steady-state count:
`replicas ≈ ceil(peak_arrivals_per_sec / admissions_per_sec_per_replica)`.

## `uvicorn --workers N` is not the goal

NiceGUI is single-worker by design. Its socket.io server is constructed with no
cross-process client manager (`nicegui/nicegui.py:50`), so a message cannot be
routed to a client held by another process, and a client's element tree lives in
the process that rendered its page. **Adding workers does not scale this app, it
corrupts it.** More capacity means more *replicas* behind sticky sessions.

Sixteen items of process-global state pin the app to one process. Six **break** at
N>1 rather than merely duplicating work:

| Singleton | What breaks |
|---|---|
| `match_live` / `check_in_live` subscribers | a change committed in A never refreshes a browser on B — live schedule, bracket and check-in desk views go silently stale |
| OAuth handoff nonce store | mint on A, claim on B → login fails; single-use stops being globally enforced |
| Per-match seed lock | two processes roll a seed for the same match, last-writer-wins |
| NiceGUI element trees + socket.io | no cross-process client manager |
| `app.storage.user` | fixable by configuration — NiceGUI 3.12 ships `NICEGUI_REDIS_URL` → `RedisPersistentDict`; compose now runs a `redis` service and sets it, and `validate_session_storage()` refuses an unreachable one at boot |
| ~~`aerich upgrade()` at boot~~ | **done** — `application/utils/migration_lock.py` wraps the upgrade in a `pg_advisory_lock` on a dedicated connection, built to the shape this document specifies below (own connection, own constant, no-op on SQLite). It was worth doing ahead of the rest: at one replica the race is still reachable through an overlapping deploy or a fast crash-loop, and a half-applied migration is the one failure here that damages data rather than degrading behaviour |

The rest duplicate or degrade: the Discord gateway, racetime connections, the eight
`BackgroundLoop` workers, the DM and dispatch queues, the API rate limiter
(effective limit becomes N × configured), the service-health cache (N alert DMs per
transition), and the tenant caches.

## Phases

**Phase 1 — cut render cost.** No infrastructure. Lazy tab panels ✅ and the
match-table load dedupe ✅ shipped. Remaining: match-table element count (16 inline
Vue slot templates, several per-permission copies of the same markup — a DRY fix
with a perf payoff, verifiable only via `/ui-validation` since slot templates are
invisible to pytest); the Schedule tab's query fan-out (prefetch in
`MatchRepository`/`MATCH_PREFETCH`, not in the view); shell cost (`render_chrome`
≈14 %, of which `_render_drawer` ≈9 % — one `q-item` per tab, 24 on admin, paid by
every page). The queries-per-render regression guard has shipped (`tests/test_query_budget.py`, [below](#measurement-discipline)).

**Phase 2 — singleton ownership.** Buys no capacity; prerequisite for 3 and 4.
"Fail fast if `workers>1`" cannot be implemented as written — a forked child cannot
read its own worker count and uvicorn's `--workers` does not set `WEB_CONCURRENCY`,
so there is no in-process value to assert on. The implementable equivalent asserts
what is actually cared about: on startup take `pg_try_advisory_lock(<constant>)`;
lock acquired ⇒ leader, start the Discord gateway, racetime runtime and the eight
workers; refused ⇒ follower, log a WARNING naming what it is skipping and start
nothing, but **stay up as a warm standby**. Postgres is already a hard dependency.

Two constraints govern the design. `pg_advisory_lock` is **session-scoped** — held
by a connection, released when it closes, which is exactly the failover semantics
wanted — so it must **not** be taken on a Tortoise-pooled connection, which is
returned and reused and would release the lock at an arbitrary moment. Use a
dedicated `asyncpg.connect()` owned by the lifespan. The migration lock must be a
**separate constant** from the leader lock: leadership is long-lived, migration is a
short critical section, and a follower must block on the schema while not blocking
on leadership. The test suite runs on SQLite, which has no advisory locks, so the
election helper needs a seam that no-ops to always-leader on non-Postgres backends.

The migration half already exists and is the worked example to copy:
`migration_lock` in `application/utils/migration_lock.py` holds
`MIGRATION_LOCK_KEY` on its own `asyncpg` connection, yields `False` instead of
locking on a non-PostgreSQL DSN, and is proven against a real backend in
`tests/postgres/test_migration_lock.py` — where the assertion is that a second
holder *cannot* enter, which is the only property worth testing and the one
SQLite can never show. The leader election needs a different constant and a
`try`-style acquire (`pg_try_advisory_lock`) rather than a blocking one.

**Phase 3 — `web`/`worker` split.** Availability, not capacity. Postgres
`LISTEN`/`NOTIFY` is the cross-process seam, chosen over adding Redis: Postgres is
already a dependency, asyncpg exposes `add_listener`, and payloads are tiny
identifiers. `match_live.publish()` additionally issues `NOTIFY wizzrobe_match_live`;
each process holds one long-lived listener that re-publishes into its own local
`_subscribers`, so `theme/realtime.py` and `theme/brackets/live.py` need **no
changes**. Ship behind an env switch, default off, so it is a no-op single-process
and can land before the split. Constraints: payloads cap at 8000 bytes and deliver
on commit (identifiers only, never a serialized model); the listener needs a
dedicated connection (same helper as the advisory lock — build it once); delivery is
at-most-once, fine for a UI nudge that self-heals, **not** acceptable for anything
transactional. `event_bus` does not need to cross the boundary, *provided the
webhook and telemetry subscribers are registered in both roles*.

Then `ROLE=web|worker|all` with `all` the default so dev/CI/`ui-validation` stay
single-process, migrations moved to the worker role, and worker liveness — the
compose healthcheck curls `/api/health`, which a worker will not serve, and an
unmonitored dead worker is worse than today.

**Phase 4 — N web replicas.** Not currently needed. Sticky sessions are
non-negotiable; there is no software workaround short of a socket.io Redis manager
NiceGUI does not wire up, so **if the deployment target cannot do sticky sessions
this phase is not viable and render cost is the only lever**. Needs
`NICEGUI_REDIS_URL` for session storage, and shared stores for the nonce store and
seed lock first (they fail silently and wrongly) ahead of the rate limiter and
tenant caches (which merely degrade). Redis already runs in compose for session
storage, so these would reuse it rather than add infrastructure.

## Measurement discipline

On the rig these numbers were taken (driver and server sharing a host) sequential
renders vary **±15 %** run to run, and the 16 tabs/s burst is past the knee and
chaotic — five runs of an identical configuration gave 92, 219, 255, 288 and
304 connections out of 500. **Anything under a ~20 % improvement cannot be validated
here.** Prefer **queries per render** as the primary metric: exact and
machine-independent, and **never assert on wall time in CI**.

The guard is [`tests/test_query_budget.py`](../tests/test_query_budget.py). It
counts statements on the default connection around
`MatchDisplayService.get_matches_for_display` — what `MatchTableView.refresh`
actually calls, unfiltered and filtered to one player — and asserts two things:

- **Shape:** the same call issues the *same* count over 3 matches and over 12.
  A dropped `prefetch_related` turns that into "grows with rows", with no magic
  constant to re-tune when the fixture changes. Verified by removing
  `players__user` from `MatchRepository.get_all` and watching it fail.
- **Ceiling:** ≤11 against a measured 9, which bounds the *other* regression
  recorded above — the same set loaded twice would be 18.

The formatter walking every prefetched relation is what makes this work. A loader
whose result nothing walks counts the same with or without its prefetches, so
guarding one proves nothing — which is why `MatchService.get_all_matches_for_schedule`
is not guarded here. (That method is also, as of this writing, unreachable: the
pages pass it to `MatchTableView` as `get_query`, which the view stores and never
calls.)

## Already tried, measurably useless

Do not re-attempt these:

- **`BaseHTTPMiddleware` → pure ASGI** for the two header-only middlewares: fully converted, headers verified byte-for-byte; 63 ms baseline vs 71 ms converted, 288/500 vs 255/500 under burst. Reverted. If revisited, the candidates are `Auth` and `Tenant`, which run real logic — not the header ones.
- **Raising NiceGUI's `response_timeout`** 3 s → 20 s: 304/500 and 219/500 against a baseline spread of 92–288. No signal for *throughput* past the knee, so it was reverted then. It is now raised to 30 s anyway (`application/utils/page_build_timeout.py`) for a different reason: a restart sends every open tab back inside a few seconds, a burst of ~25 just past the admission ceiling, and the builds that needed 4–5 s were being cancelled at 3 s into a dead-end error page instead of finishing.
- **Disabling TLS on the DB connection**: the profile showed heavy `asyncio.sslproto` and ~1200 HMAC ops per render, but that is a test-environment artefact — Debian's packaged Postgres enables `ssl=on` with a snakeoil cert, while `postgres:16-alpine` (what compose runs) ships no server certificate, so production is already un-TLS'd. Every CPU figure here is therefore slightly *pessimistic* relative to the real deployment.

## Traps in the remaining Phase 1 items

**Caching `TenantService.get_by_id`** (4 identical fetches per render; the one hot-path
lookup not already cached alongside `get_by_slug`/`get_by_guild`/`get_by_domain`)
would serve a **stale theme after a save**: `TenantThemeService.set_theme` writes via
`TenantRepository.update(...)` directly, bypassing `TenantService._clear_cache()`, and
`get_current_theme`'s docstring explicitly relies on that read being uncached. Any
implementation must first route every direct `TenantRepository.update` caller through
an invalidating path.

**Memoizing `get_user_from_discord_id`** (6 lookups per render) must **not** use a
module-level cache — CLAUDE.md forbids module-level per-user state and it would be a
genuine cross-user leak. A correct fix needs request-scoped storage
(`app.storage.client`, or threading the resolved `User` through the call chain) plus
a decision about staleness within one render.

## Open questions gating the sizing work

All three are answerable from data already collected:

1. **What is the real arrival pattern for 500?** The plan assumes a crowd (a race goes live, a bracket is published, a Discord announcement fires) because that is the hard case. If 500 is a slow accumulation over an evening, only memory needs sizing and Phase 4 may never be needed. Answerable from telemetry page-view timestamps.
2. **How many tabs does one viewer open?** 500 connections is not 500 people if spectators keep the bracket and schedule side by side; this changes the sizing denominator.
3. **Is Redis acceptable in the real deployment?** Compose already runs it for session storage; the question is whether production does too. It is the cleanest answer for the shared nonce and rate-limiter stores. If not, the ceiling is one web replica.
