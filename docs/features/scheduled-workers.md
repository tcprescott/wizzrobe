# Scheduled workers

Every periodic background worker (a `run_worker_loop` module) is listed once, in
`application/worker_registry.py` `WORKERS`, with a **placement** and an optional
env gate. `main.py`, `scripts/run_worker_tick.py` and the Cloud Scheduler tick
endpoint all read that list; `tests/test_registry_drift.py` fails when a worker
module is missing from it or `main.py` starts one by hand.

## Two modes (`WORKER_MODE`)

| Mode | Who runs a `scheduled` worker | Who runs `bot` / `per_process` workers |
|---|---|---|
| `loop` (default, today's production) | the one app process, as an in-process `BackgroundLoop` | the same process |
| `scheduled` | Cloud Scheduler, via `POST /internal/workers/<key>/tick` on any API instance | still in-process loops |

Placements:

| Placement | Workers | Why |
|---|---|---|
| `scheduled` | everything else | DB-driven ticks any instance can run |
| `bot` | `race_room`, `service_health`, `telegram_bot` | need the racetime room bot's websocket, Telegram long-polling, or the in-memory health cache; they move with the bot service (spec step 4) |
| `per_process` | `metrics_sampler`, `worker_watchdog` | read the process they run in |

A spec's `boot` hook runs at startup in both modes: `discord_role_sync` subscribes
to the event bus there, so events still mark communities for its next tick.

## The tick endpoint

`api/worker_ticks.py`, mounted before NiceGUI in `main.py`, outside `/api` and
the OpenAPI schema.

- In `loop` mode every path is a 404.
- Auth: `Authorization: Bearer <Google OIDC ID token>`. `application/utils/google_oidc.py`
  checks the RS256 signature against Google's published keys (cached per their
  `Cache-Control`, one refetch a minute for an unknown key id), the issuer, expiry,
  `aud == WORKER_TICK_AUDIENCE` and `email == WORKER_TICK_SERVICE_ACCOUNT` with
  `email_verified`. Missing config is a 503; a bad token a 401.
- A tick runs through `BackgroundLoop.run_once`, the same code the loop uses, so
  the timeout, Sentry reporting and `worker.tick_duration` metrics are identical.
- **Burst:** Cloud Scheduler fires at most once a minute, so a worker faster than
  that (seed roll 5s, data export 5s, role sync 15s) ticks at its own interval
  inside the call until `WORKER_TICK_WINDOW_SECONDS` (55) is up. Thomas picked
  this on 2026-10-31 so seed rolls keep today's speed.
- Responses: 200 `{"ticks", "failures": 0}`; 200 `{"skipped": "disabled"}` when the
  worker's env gate is off; 200 `{"skipped": "running"}` when a tick for that worker
  is still running on the same instance; 500 when any tick failed.

`poetry run python scripts/run_worker_tick.py --list` prints each worker's
placement and the cron its Scheduler job should use (`cron_schedule`).

## Not covered yet

- **Overlap across instances.** The "already running" guard is per instance. Two
  instances can run the same long tick (role sync, retention) at once if a call
  outlasts its period. Needs the shared lock from spec step 2.
- **Per-process state some ticks keep:** `discord_role_sync` keeps its pending set
  and last-full-sync times in memory, so a fresh instance does a full pass and a
  community marked on another instance waits for the next full pass;
  `data_export` prunes more often; `sglive_pool` keeps its last error in memory.
- **Discord-calling ticks** (reminders, Discord events, role sync) still go through
  `DiscordService`, which today needs the gateway client in the same process.
  Step 4 (Discord over HTTP) has to land before those run on Cloud Run.
- **Scheduler jobs themselves** are created in step 5 (Cloud Run deploy).
- **Seed roll and data export polling move to Cloud Tasks in step 5** (Thomas,
  2026-10-31): a slow roll or a queued export enqueues its own follow-up check
  instead of a minute burst that keeps an instance awake while idle.
