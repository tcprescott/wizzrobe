---
name: incident
description: >-
  Diagnose and fix a live production problem in Wizzrobe fast — the app is
  slow, frozen, erroring, or a worker has gone quiet — during an event. Reads
  live load (the super-admin `runtime_stats` MCP tool), Sentry issues, traces,
  profiles and logs, narrows the cause to a file and line, reproduces it
  locally, and ships the smallest safe fix as a PR. Use whenever someone
  reports production trouble ("the schedule is slow", "pages won't load",
  "people are getting errors", "DMs stopped"), or asks to investigate a Sentry
  alert or a loop-stall issue.
---

# Production incident

Production is one container on one asyncio loop (`docs/scaling-roadmap.md`).
That shapes every diagnosis: a slow request is rarely the problem on its own,
and one synchronous stretch in any handler freezes every user. Work in this
order and stop as soon as the cause is clear. Speed matters more than polish,
but a fix that turns CI red costs more time than it saves.

## 1. Look before touching code

Run these together, in one turn:

- **`runtime_stats`** (Wizzrobe MCP, super admin, no `tenant`). Read it like this:
  - `event_loop.recent_stalls` not empty: something blocked the loop, and
    `culprit` is the file and line. That's the fix target. Go to step 3.
  - `lag_ms_p95` high, no stalls, `cpu_percent` near 100: the app is saturated
    by load (renders are CPU-bound, about 10 new tabs a second per process).
    Look at `clients.connected` and `watch_pages_by_tenant_id.*.arrivals_last_minute`.
    The fast lever is static mode on the Watch page (Admin → Settings → Watch
    page) and pointing crowds at static `/live/…` bracket links, not code.
  - `db_pool.idle` at 0 with `size` at `max`: queries are queueing. Find the
    slow query in Sentry traces.
  - A worker with `failures` climbing, or `last_started_at` far older than its
    `interval_seconds`: that worker is broken or stuck. See
    `/worker-validation` for exercising its tick locally.
- **Sentry** (connector): unresolved issues in production for the current
  `release`, newest first; any issue tagged `loop_blocked_in`; traces for the
  slow page (sort by duration) and their profiles; Sentry Logs filtered by
  `tenant_id` around the reported time.
- **`list_service_health`** for the community, when the symptom smells like an
  outside service (racetime, Challonge, Discord, seed generators).

If a tool is missing or refuses (Sentry not signed in, connection not super
admin), say so to the person in one line and carry on with what you can read.
Never guess at production state.

## 2. Say what you found

Before writing code, reply with the cause in one or two sentences and the
evidence (the stall culprit, the trace, the issue link), plus any lever staff can
pull right now without a deploy (static mode, or turning a module's logging up on
Platform → Logging). They may need that lever
more than the fix.

## 3. Reproduce

- A loop stall or slow render: `scripts/loadtest/profile_render.py` cProfiles a
  page render in-process; point `PATH` at the slow page. `scripts/loadtest/ws_load.py`
  reproduces the admission ceiling. Setup in `scripts/loadtest/README.md`.
- A query fan-out: `tests/test_query_budget.py` shows how to count queries per
  render. Add a budget for the page you fix.
- An error: write the failing test first from the Sentry stack.

## 4. Fix narrowly

- The usual culprits on this loop: a sync call in an async path (`requests`,
  `time.sleep`, heavy CPU in a handler, a big `json`/markdown render per row),
  a per-row query inside a render loop (move it into the repository's
  prefetch), or a table rebuilt in full on every live refresh.
- CPU-heavy work that can't shrink goes in `await asyncio.to_thread(...)`.
- No feature work and no refactors mid-event. Keep the diff to what the
  incident needs, and leave a follow-up note for anything bigger.
- If the fix touches a migration, say so loudly: rollback won't undo it
  (`docs/deployment.md#rollback-caveat`), and Thomas needs a DB backup first.

## 5. Ship

- Run the fast checks for what you touched (the relevant pytest files, ruff,
  the guardrail hooks fire on their own), then push and open the PR, then
  subscribe to it and drive CI to green.
- In the reply, give the merge-and-deploy steps: after merge, the
  `Publish Container` workflow builds `ghcr.io/tcprescott/wizzrobe` (usually
  5 to 11 minutes); then on the host, the Upgrade steps in
  `docs/deployment.md` (`docker compose pull wizzrobe` when the compose file
  points at the GHCR image, then `docker compose up -d wizzrobe`).
  A redeploy keeps sessions (Redis) but drops open sockets for a few seconds.
- After the deploy, read `runtime_stats` and Sentry again to confirm the stall
  or error is gone under the new `release`.

## When an outside service is down

Most outages need staff to keep going, not a code fix. Tell them the lever below
first, then check whether the app made it worse. Find which service from
`list_service_health` / `platform_service_health`, `runtime_stats` (queue depths
and worker failures) and Sentry. During any outage, **don't restart or redeploy
unless the app itself is stuck**: a restart drops queued DMs and webhook
deliveries (the queues live in memory and drain for only 5 seconds), and every
open page reloads.

### Discord (bot, DMs, login)

- What still works: everyone already signed in, the schedule, check-in, seeds.
  DM copies still go out by push, email and Telegram. New logins fail with
  "Discord isn't answering right now"; anyone with a password uses
  `/password-login`.
- DMs fail fast while the bot is disconnected and are **not retried**. Stage,
  volunteer and community-event reminders wait (unstamped) and go out after the
  reconnect if still in their window. Watch `runtime_stats` → `queues.discord_queue`.
- The bot retries its login every 30s on its own; a gateway drop resumes by itself.
  If Sentry shows "Discord bot task exited" (bad token), fix `DISCORD_TOKEN` and restart.
- After: call the matches and shifts that fell due during the outage by hand,
  and re-send check-in test DMs showing "Not tested (Discord down)".

### Challonge

- Booking and confirming keep working; confirmed results are saved in Wizzrobe
  but the push to Challonge fails and is **not retried**. List them from the audit
  log (`match.confirmed` during the outage window, no `challonge.result_pushed`).
- Sign-up and withdraw show "Challonge isn't answering right now".
- A sync that gets an empty or partial answer now refuses instead of pruning.
- After: report the missed results on Challonge by hand, press Sync once, and
  check the Players list and booked matches look right. Reconnect under
  Admin → Challonge if calls fail with 401.

### start.gg

- The sync worker re-reads every 2 minutes and recovers by itself; a null answer
  now errors instead of wiping the mirror. Results confirmed during the outage are
  not reported: report them on start.gg by hand afterwards.
- 401/403 means `STARTGG_API_TOKEN` expired: replace it and redeploy.

### Matcherino (check-in roster)

- The desk shows the sync error and keeps the last roster. Keep checking people in
  and add walk-ups by hand. "Refused login" means paste a fresh refresh token on
  Admin → Check-in (from a private window). Press Sync once it's back.

### SpeedGaming

- Imported matches stay. The link shows `error` with the reason. An empty answer no
  longer cancels every episode. Press Sync now on each link once it's back.

### racetime.gg

- "Create racetime room" fails with a reason within 15s. Don't click repeatedly:
  each click can open a duplicate room on racetime.gg.
- To run racetime-tournament matches on site without racetime, clear the
  tournament's racetime bot in Tournament edit; Check in, Roll seed, Start,
  Finish and Confirm come back. Re-roll a racetime match's seed with the MCP
  `generate_match_seed` tool (the UI has no button for those).
- After: re-set the bot, open rooms by hand for matches more than 15 minutes past
  start (auto-open won't retry them), close duplicate rooms on racetime.gg, and
  restart the bot (`platform_restart_racetime_bot`) if health still says error.

### A randomizer (alttpr.com, DK64, OoTR, …)

- Roll seed fails with "did not respond", "Could not reach…" or "alttpr.com didn't
  make the seed". A failed roll writes nothing, so retrying is safe. Fallback: roll
  on the randomizer's own site and post it to the players.
- DK64 rides out short blips (the poll retries). A roll stuck "in progress" after
  a restart frees itself after 5 minutes.
- After: look for started matches with no seed and `match.seed_roll_failed` in the
  audit log.

### Database

- Users see the error page; workers log one error per tick (expect a Sentry burst).
  The app reconnects by itself once Postgres is back, so **don't restart it**.
- After: re-check anything staff did during the outage; for native brackets, look
  for matches COMPLETE whose winner never advanced.

### Redis (sessions)

- People with a tab open keep working; new visitors look signed out and can't log in.
- The app **won't boot** with Redis down. If you must restart, remove
  `NICEGUI_REDIS_URL` from the environment (everyone signs in again).
- After `docker compose restart redis`, affected users reload.

### Email (SendGrid), Telegram, web push, a webhook receiver, Sentry, Twitch

- Each is a copy, not the primary channel; Discord DMs and the app are unaffected.
  If `runtime_stats` → `queues['event dispatch']` keeps climbing, unset
  `SENDGRID_API_KEY` / `TELEGRAM_BOT_TOKEN` / `VAPID_PRIVATE_KEY` and restart.
- A dead webhook receiver only slows its own queue (`webhook delivery`). Find it in
  Admin → Webhooks' delivery log (`list_webhook_deliveries`) and deactivate it.
- Sentry down: nothing to do; it never blocks the app. Use container logs and
  `runtime_stats`. Twitch down: Watch-page players go blank in the browser only.

### Restarting mid-event

- Deploy between rounds, never while seeds are rolling or results are being
  reported. Take a DB backup before any release with a migration.
- `docker compose up -d wizzrobe` is 10-40s down. Pages reconnect for 30s, then
  reload (half-typed dialogs are lost). The compose file gives shutdown 30s.
- After: check the bot is connected on /platform health, and confirm seed DMs for
  any match rolled around the restart (the seed is also on the player's page).
- `docker ps` showing `unhealthy` restarts nothing on its own: restart it by hand.
