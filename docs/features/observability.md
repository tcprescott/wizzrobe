# Observability: Sentry and runtime log levels

_How errors and logs leave the process, what context they carry, and how a
super-admin turns a module's logging up mid-event. Part of the
[documentation index](../README.md). Env vars are in the
[deployment table](../deployment.md#environment-variables)._

## What reaches Sentry

[`application/utils/sentry.py`](../../application/utils/sentry.py) initializes the
SDK once, before the FastAPI app is built, and only when `SENTRY_DSN` is set.

| Signal | How it gets there | Notes |
|---|---|---|
| **Error events** | any `logger.error` / `logger.exception`, plus unhandled exceptions the FastAPI/Starlette integration sees | `LoggingIntegration(event_level=ERROR)`. A `logger.warning` is a breadcrumb only, so a failure someone relied on must log at ERROR. |
| **Breadcrumbs** | every INFO+ record before an event | Attached to the next event in the same scope. |
| **Sentry Logs** | every INFO+ record (`SENTRY_LOGS_LEVEL`) | Searchable alongside events. `uvicorn.access`, `httpx` and `httpcore` are excluded (one line per request). DEBUG/TRACE never ship, whatever a logger is set to. |
| **Traces** | `SENTRY_TRACES_SAMPLE_RATE`, default `0.1` in production and `0` elsewhere | Slow pages, slow DB and outbound HTTP calls. HTTP only: a NiceGUI button click arrives over the websocket and is never traced, which is what the [watchdog](#blocked-event-loop-watchdog) covers. |
| **Profiles** | `SENTRY_PROFILES_SAMPLE_RATE`, default `1.0` (of traced requests) in production | Which functions a slow traced request spent its CPU in. |
| **Loop stalls** | the [watchdog](#blocked-event-loop-watchdog) | WARNING from 0.5s, an ERROR issue from 2s, grouped by the blocking line. |
| **Metrics** | [`sentry_metrics.py`](../../application/utils/sentry_metrics.py); on with Sentry unless `SENTRY_METRICS=off` | See [Metrics](#metrics). Explore > Metrics in Sentry. |
| **Health alerts** | `ServiceHealthService._alert` → `capture_message` | Fingerprinted on probe + status, tagged `health_probe` / `health_status`, so one outage is one issue. `down` alerts only after two consecutive failed checks (~2 min apart), so a one-off timeout stays on the board without opening an issue; credential warnings alert at once. |
| **Browser errors + replays** | the JavaScript SDK on every page, same DSN | See [Browser errors](#browser-errors-platform--monitoring). Tagged `surface: browser`; server events carry `surface: server`. |

Every event carries `environment`, `release` (`SENTRY_RELEASE`, else
`wizzrobe@<GIT_SHA[:12]>`; `publish.yml` bakes `GIT_SHA` into the image), and
`send_default_pii=False`; `before_send` scrubs auth headers and cookies and drops
the NiceGUI timer-teardown race and uvicorn's graceful-shutdown cancellations
(`Cancel N running task(s)` and the `CancelledError` per request still open at
the 10s deadline, mostly socket.io long-polls on every deploy). Each cancelled
request is still named at WARNING on `wizzrobe.shutdown` in Sentry Logs, and the
lifespan shutdown calls `flush_sentry()` so those last log lines leave the
process before it exits.

One more restart artefact is fixed at the source rather than filtered: a
socket.io long-poll for a session engine.io has just closed raised
`KeyError('Session is disconnected')` out of `handle_request` and ended as a 500
(SGLMAN-1P). `application/utils/socketio_closed_session.py` wraps NiceGUI's
engine.io server so that request gets the 400 engine.io answers for any stale
session, and the browser reconnects. The same wrapper answers 400 when the browser left
before engine.io read the request, which otherwise failed on
`environ['REQUEST_METHOD']` (SGLMAN-1T/1V).

## Context on every event

`before_send` adds two things the SDK can't know:

- **`tenant_id` tag** from `get_current_tenant_id()`. It's set for HTTP requests
  (`TenantMiddleware`), UI handlers (the client stash), and anything inside
  `tenant_scope(...)` — workers, queues, bot handlers. Logs get the same value as
  a `tenant_id` attribute (`before_send_log`).
- **User**, when the event has none. `AuthMiddleware` sets the user on the HTTP
  request's scope, but a button click arrives over the websocket after that
  scope is gone; the hook falls back to `app.storage.user`.

Surfaces that run outside a request add their own tags with
`sentry_tags(**tags)`, a context manager that forks the scope so the tags vanish
on exit (`tag_current_scope` adds one learned mid-block):

| Surface | Tags |
|---|---|
| `BackgroundLoop` (every worker tick) | `worker` |
| `for_each_tenant_scoped` (per item) | `work_item`, plus `tenant_id` (the failure is logged inside the tenant scope) |
| `CoroutineQueue` (Discord DM queue, event dispatch) | `queue`, `queued_call` (the inner call, e.g. `_run_in_tenant_scope(send_dm)`) |
| Discord DM buttons (`discordbot/_ack_common.py`) | `discord_interaction`, `discord_custom_id`, `discord_user_id`, `tenant_id` |
| racetime room handler | `racetime_room`, `racetime_category`, `tenant_id` |
| MCP tools | `mcp_tool`, `tenant_slug` |
| NiceGUI 500 page | `error_id` (the UUID shown to the user) |

## Failures that log at ERROR

These used to log at WARNING or not at all, so Sentry never heard about them.

| Failure | Where |
|---|---|
| Async seed roll failed, abandoned, or finished with no match | `seed_roll_service.py`, `provider_task_service.py` |
| Seed generation failed after retries | `match_schedule_service.py` |
| racetime room refused / not recorded after opening | `race_room_service.py` |
| racetime bot auth failure (bot stops until restart) | `racetimebot/connection.py` |
| SpeedGaming fetch failed | `speedgaming_etl_service.py` |
| Matcherino check-in sync failed | `check_in_sync_worker.py` |
| Challonge post-push re-sync failed | `challonge_service.py` |
| Discord DM not sent because the bot is down, or crashed on our side | `discord_service.send_dm` |
| Discord guild/event op crashed on our side (a Discord refusal stays WARNING) | `discord_guild_ops.log_discord_failure` |
| Series game unresolved with no match | `_bracket/series.py` |
| Invalid `VAPID_PRIVATE_KEY` | `web_push_service.py` |

Deliberately left at WARNING: a DM a user has blocked (`Forbidden`), a seed
provider attempt that will be retried, a racetime reconnect, a community's
webhook endpoint failing (the delivery row is what staff act on), web-push
endpoint errors.

## Runtime log levels (`/platform` → Logging)

A super-admin can set any stdlib logger to `TRACE`, `DEBUG`, `INFO`, `WARNING`,
`ERROR` or `CRITICAL` without a redeploy. The table lists the loggers worth
reaching for (`application`, `application.services.discord`, `racetimebot`,
`discordbot`, `tortoise`, …) with what each covers, the stored level, and the
level it actually runs at (inherited from a parent when unset); **Other logger**
takes any dotted name.

- A change applies to the live process at once and is stored as a
  [`LogLevelOverride`](../reference/data-model.md#logleveloverride) row, which
  `main.py` reapplies at startup, so a restart mid-event keeps it. A row that no
  longer parses is skipped with an ERROR rather than blocking boot.
- **Reset** deletes the row; the logger goes back to inheriting (root goes back
  to `LOG_LEVEL`).
- Each change is audited (`log_level.set` / `log_level.cleared`, platform-level,
  never webhooked) and logged at WARNING so it's visible in Sentry Logs.
- Levels gate what a logger *emits*. DEBUG and TRACE land in the container log
  only; Sentry Logs stays at INFO+. `TRACE` is level 5, registered at startup
  (`register_trace_level`), so `LOG_LEVEL=TRACE` also works.
- Not feature-flagged: it's a platform surface gated on `SUPER_ADMIN`.

Code: [`application/utils/log_levels.py`](../../application/utils/log_levels.py)
(levels, known loggers, applying to the tree),
[`LogLevelService`](../reference/services.md#log_level_servicepy--loglevelservice),
[`pages/platform_logging.py`](../../pages/platform_logging.py).

## Browser errors (`/platform` → Monitoring)

People's browsers report to the same Sentry project as the server, through the
JavaScript SDK (errors + Session Replay, no tracing). It loads on every page
built by `BaseLayout`, `render_platform_chrome` or the waiting screen, and only
when `SENTRY_DSN` is set and browser errors are switched on.

- **The bundle is vendored**, `static/vendor/sentry/bundle.replay.min.js`
  (`@sentry/browser` 11.4.0), so venue Wi-Fi and CDN blocklists don't decide
  whether it loads. Bump it by downloading the same file name from
  `browser.sentry-cdn.com/<version>/`.
- **What it catches.** A websocket UI hides most browser errors from
  `window.onerror`: Vue hands render and slot-template errors to
  `app.config.errorHandler`, and NiceGUI only `console.error`s an error raised
  while it applies a server update. [`static/js/sentry-browser.js`](../../static/js/sentry-browser.js)
  hooks both (tagged `caught_by: vue` / `console`) on top of the SDK's own
  uncaught-error and unhandled-rejection handlers.
- **Replays** buffer the last minute in memory and upload only with an error,
  with all text and inputs masked and media blocked. The tab reports its replay
  id to the server (`wiz_sentry_replay` → `app.storage.client`), and
  `_enrich_event` puts it on any server event raised in that tab's websocket
  handlers. When a tap fails on the server, `frontend.py`'s catch-all tells the
  tab to upload its buffer, so the server's issue links to a recording of what
  was tapped.
- **Noise never leaves the browser.** `allowUrls` limits events to stacks in our
  own scripts; `beforeSend` drops anything matching an ignore rule (a
  case-insensitive substring of the message or of a stack frame's URL). The
  built-in rules (`application/utils/browser_monitoring.py`) cover extensions,
  dropped connections and browser quirks; super-admins add the rest.
- **Runtime controls**, `/platform` → Monitoring (super-admin): send on/off,
  replay on/off, share sent (100/50/25/10%), and the ignore rules. Changes are
  pushed to every open tab at once; turning sending back on reaches a tab on its
  next page load, since a tab built while it was off never loaded the SDK.
  Stored in `BrowserMonitoringSettings` / `BrowserErrorIgnoreRule` (global),
  loaded at startup into an in-memory snapshot the page builds read.
- Not feature-flagged: it's a platform surface gated on `SUPER_ADMIN`.

To silence a noisy browser error, add a rule from its Sentry title rather than
resolving the issue in Sentry.

Code: [`BrowserMonitoringService`](../reference/services.md#browser_monitoring_servicepy--browsermonitoringservice),
[`theme/browser_monitoring.py`](../../theme/browser_monitoring.py),
[`pages/platform_monitoring.py`](../../pages/platform_monitoring.py).

## Blocked event loop (watchdog)

Every page, socket, worker and bot handler shares one asyncio loop, and render
cost is CPU-bound ([scaling-roadmap.md](../scaling-roadmap.md)), so the failure
that hurts most is one synchronous stretch that freezes everyone. The request
that caused it finishes normally and the starved ones only look slow, so nothing
used to log it.

[`application/utils/loop_watchdog.py`](../../application/utils/loop_watchdog.py)
runs a daemon thread, started at the end of boot, that posts a heartbeat onto the
loop four times a second. A heartbeat late by 0.5s makes the thread sample the
loop thread's stack while it is still blocked, which is what names the culprit:
the innermost frame in this repo (`pages/admin.py:412 in _render_rows`, say),
not the library line underneath it.

| Stall | Goes to |
|---|---|
| 0.5s to 2s | WARNING on `application.utils.loop_watchdog` (Sentry Logs) |
| 2s or more | ERROR, so a Sentry issue fingerprinted `event-loop-blocked` + culprit and tagged `loop_blocked_in` |
| still blocked at 10s | reported immediately, since it may never return |

Each log line carries the sampled stack. `LOOP_WATCHDOG_ENABLED=false` turns it
off.

### Garbage-collection pauses

A full CPython garbage collection also stops the loop, and it starts on whatever
allocation tips a generation over its threshold, so a sampled stack lands on an
innocent line (a label's constructor, a dataclass `__init__`, a weakref
callback). [`gc_monitor.py`](../../application/utils/gc_monitor.py) hooks
`gc.callbacks` and times every pass; when at least half of a stall was
collection, the watchdog reports it as `garbage collection (gen N, X.XXs)`
instead, all such stalls group into one Sentry issue
(`event-loop-blocked` + `garbage collection`), and each stall in `runtime_stats`
carries `gc_seconds`. `runtime_stats` also has a `gc` block: passes, total and
worst seconds per generation, the slowest recent passes, thresholds, pending
counts.

Most of what a full pass walks is page element trees: about 25,000 objects per
open tab, and a closed tab's tree is a reference cycle that only a full pass
frees. The other big share is what start-up loaded (about 370,000 objects), so
`main.py` calls `gc_monitor.freeze()` once boot is done: those objects move to
the permanent generation and no pass walks them again. In a local event-size run
that cut the worst pause from 0.78s to 0.51s. `GC_FREEZE=false` turns it off.
Raising the collection thresholds was measured too and rejected: fewer passes,
but each one longer. `BackgroundLoop` also times every worker tick and logs a tick over 30s at
WARNING.

## Worker self-healing and the worker watchdog

Every periodic worker is a `BackgroundLoop` (`run_worker_loop(...)`) and every
in-memory queue is a `CoroutineQueue`. Both register themselves when built, so a
new worker gets all of this by using them; don't hand-roll a `while True` task.

| Fault | What happens |
|---|---|
| A tick raises | Logged (`"{name} tick failed"`), counted in `worker.tick_failures`, next tick runs on time |
| A tick runs past its limit (`TICK_TIMEOUT_SECONDS`, 300s; per worker `tick_timeout=`) | Cancelled, counted as a failure and a timeout, ERROR fingerprinted `background-worker-fault` + worker |
| A queued call runs past `ITEM_TIMEOUT_SECONDS` (300s) | Cancelled so the calls behind it run; same ERROR |
| A `CancelledError` leaks out of a tick or queued call | Treated as a failure; the loop carries on (only `stop()` ends it) |
| The loop or queue task ends any other way | ERROR, then restarted after 5s, doubling to 60s while it keeps dying |
| None of that worked (a tick that swallows its cancel, a restart that never runs) | The `worker watchdog` loop (every 30s, [`worker_watchdog.py`](../../application/utils/worker_watchdog.py)) logs one ERROR per stalled worker, `Background worker X is stalled: …`, and a WARNING when it recovers |

A worker counts as stalled when it is enabled and has gone `interval +
STALL_GRACE_SECONDS` (60s) without a tick, or a tick or queued call has run 60s
past its limit. A worker switched off by config (`enabled: false` in
`runtime_stats`) is never stalled.

### Finding a leak (`memory_census`)

Gen-2 passes that keep getting longer mean the live heap is growing. The
super-admin MCP tool `memory_census`
([`memory_census.py`](../../application/utils/memory_census.py)) runs a full
collection first (`collect`, on by default, so only reachable objects count),
then counts every tracked object by type and reports NiceGUI pages still in
memory against pages still served (`clients_alive` vs `clients_registered`),
elements of deleted pages still in memory, and `match_live` / `check_in_live`
subscribers. For up to `explain` leaked pages and deleted elements it walks
back through their referrers and returns the chains, counted by shape
(`stale_clients_held_by`, `deleted_elements_held_by`): the first entry is
usually the leak. Take two some minutes apart; the types that keep rising are
what leaks. Each call walks the heap several times, so the watchdog will log it
as a stall; don't poll it.

The first leak it found (SGLMAN-29) was per-tab state in a
`WeakKeyDictionary[Client, …]` whose value pointed back at its client, so the
weak key never died and every closed tab's page stayed in memory. Per-tab state
must let go in `client.on_delete` (a disconnect alone isn't enough: a page can
be deleted without one). A handful of deleted pages held only by a shared
`refreshable`'s target list is normal; NiceGUI prunes those on its next call.

## Live load (`runtime_stats`)

The super-admin MCP tool `runtime_stats` reads the process without shell access:
uptime and release, RSS, asyncio task count, NiceGUI clients (held and with a
live socket), distinct signed-in people online (per community too), the DB pool, the watchdog's last minute (lag p50/p95/max, process
CPU where 100 is one core, the last 20 stalls with their culprit), every worker's
enabled/running/stalled state, tick count, failures, timeouts, restarts and
last/max/current tick time, in-flight HTTP requests, each coroutine queue's
worker (Discord DMs, event dispatch, webhook delivery: depth, current call
time, timeouts, restarts, stalled), and each community's
Watch-page audience. Healthy at rest is a p95 lag of a few milliseconds and no stalls; CPU
pinned near 100 with a rising p95 is the admission ceiling the scaling roadmap
describes. Code: [`application/utils/runtime_stats.py`](../../application/utils/runtime_stats.py).

## Metrics

The same load figures go to Sentry Metrics so they can be graphed and read back
after a slow afternoon. Production only in practice (they need `SENTRY_DSN`);
`SENTRY_METRICS=off` turns them off.

| Metric | Type | Attributes | Source |
|---|---|---|---|
| `process.cpu` (percent, 100 = one core), `process.memory_rss` (MB), `process.asyncio_tasks` | gauge | | `metrics_sampler`, every 30s |
| `loop.lag_p50` / `lag_p95` / `lag_max` (ms) | gauge | | watchdog's last minute |
| `loop.stalls` | counter | | new stalls since the last sample |
| `gc.pause` (s) | distribution | `generation` | every garbage-collection pass of 10ms or longer since the last sample (from `gc_monitor`); gen-2 p95/max rising with `process.memory_rss` is a leak |
| `gc.seconds` | counter | `generation` | collection time since the last sample, all passes |
| `clients.open`, `clients.connected` | gauge | | NiceGUI tabs held / with a live socket |
| `http.in_flight` | gauge | | requests open at sample time |
| `users.online` / `users.online_by_community` | gauge | `community` (slug) on the second | distinct signed-in people with a live tab; a tab belongs to the `/t/<slug>` it's on |
| `users.active_15m` / `_1h` / `_24h`, and each `…_by_community` | gauge | `community` (slug) on the per-community ones | `active_users_metrics` worker every 5 min: distinct users with any telemetry row in the window (from the DB, so restarts don't reset it; off when telemetry is) |
| `db_pool.size`, `db_pool.in_use`, `db_pool.max` | gauge | | Tortoise pool |
| `queue.depth` | gauge | `queue` | each `CoroutineQueue` |
| `watch.live_viewers`, `watch.static_readers`, `watch.arrivals_per_minute` | gauge | `tenant_id` | Watch page audience |
| `http.requests` | counter | `route`, `method`, `status` (`2xx`…) | `RequestMetricsMiddleware`, every request |
| `http.request_duration` (ms) | distribution | same | same |
| `worker.tick_duration` (s) | distribution | `worker` | `BackgroundLoop`, every tick |
| `worker.tick_failures` | counter | `worker` | same |

`route` is the route template (`/t/{slug}/admin`), never the filled-in path, and
unmatched paths share one `unmatched` label. Requests under `/_nicegui` (assets
and the socket.io transport) are skipped: a long-poll held open by design would
swamp the timings, and tab concurrency is already `clients.connected`. A
NiceGUI button click travels over the websocket, so it is not a request here.

## During the event

A Claude session handed a production problem should follow the
[`incident` skill](../../.claude/skills/incident/SKILL.md); the steps below are
its short form.


1. Filter Sentry by `release` to see what a deploy changed, and by `tenant_id`
   or `worker` to narrow a spike.
2. For a quiet failure, search Sentry Logs around the time for the tenant; the
   INFO trail is there even when nothing errored.
3. "It's slow": read `runtime_stats` first. Stalls name the code to fix; a high
   p95 with no stalls and CPU near 100 is load, not a bug; neither points at the
   DB or an outbound call, so look at Sentry traces for that page.
4. Need more detail from one module? Set it to DEBUG on `/platform`, read the
   container log, then Reset. Don't leave `tortoise` at DEBUG: it logs every
   query.
