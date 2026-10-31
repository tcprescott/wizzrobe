# Wizzrobe as a mobile app on a serverless backend

Status: Building · Size: full · PRs: #760 (spec), app skeleton (step 6) · Requested: 2026-10-31, Thomas

## Problem

Wizzrobe's whole interface is NiceGUI: every open tab holds a live websocket and a
server-side copy of its page (about 67,000 lines across `pages/` and `theme/`, 24
page routes, most with many tabs). That's why it can only run as one long-lived
process, why phones get "connection lost" on venue Wi-Fi, and why there's no app
in the App Store or Play Store. Thomas wants a full-featured mobile app with a
rebuilt frontend, and a backend that can run serverless.

## Scope

In:
- A new frontend in `web/` in this repo: one React + TypeScript app for phone and
  desktop, installable as a PWA and wrapped with Capacitor for iOS and Android.
- Every screen that exists today, for every role (player, crew, volunteer, proctor,
  check-in desk, broadcast tech, staff, platform admin), rebuilt on the Studio design
  system with our own components (Radix headless parts styled with Studio tokens).
  Admin screens are built last.
- The REST API extended to cover every screen, with a typed TypeScript client
  generated from its OpenAPI schema.
- Live screens (run sheets, check-in desk, brackets, Watch) kept current through
  Postgres LISTEN/NOTIFY streamed to the app as server-sent events.
- The backend made stateless and deployed to Google Cloud Run (API service), with
  background workers as Cloud Scheduler jobs and one small always-on bot service.
- Push notifications: web push (already built) plus native push through APNs/FCM
  in the store apps.
- One switch-over before SGL27: every screen and the new backend go live together,
  and NiceGUI is retired that day.

Out:
- Changing what any screen does. This is a rebuild of today's behaviour; feature
  changes go in their own specs, built in the new app.
- GraphQL mutations (GraphQL stays read-only for outside integrations).
- A separate repo for the app.
- Offline editing. Screens cache what they last showed, but changes need a
  connection.

## Flow

The roles keep today's workflows. What changes for each person:

1. **Everyone.** Install from the App Store / Play Store, or "Add to Home Screen"
   from the browser, or keep using the website (same app). Sign in with Discord
   (password sign-in stays for the accounts that have it). Pick a community and
   event as today.
2. **Player.** Home opens on "Needs you" (pick a time, confirm, check in) then
   today's matches. A DM's link button opens the exact screen in the app
   (universal / app links), or the website when the app isn't installed.
3. **Crew, volunteer, proctor, check-in desk.** Their screens sit under the role
   area of the shell (see Mockup pick). Run sheets update live without a reload.
4. **Staff.** Same admin screens as now, rebuilt last, same layout on phone and
   desktop.
5. **Outside integrations.** REST, GraphQL, MCP and webhooks keep their contracts.
   The six webhook events the SpeedGaming automation consumes don't change.

## Mockup pick

**A, five tabs.** Bottom tabs on phone (Home, Schedule, Event, Work, Me), the same
five down a left rail on desktop; Work holds whichever roles you have. Options shown
at sign-off:

- **A, five tabs:** bottom tabs on phone (Home, Schedule, Event, Work, Me), the same
  five down a left rail on desktop; Work holds whichever roles you have.
- **B, mode switch:** Playing / Crew / Staff switch picks the tab set.
- **C, closest to today:** one grouped menu, drawer on phone, pinned on desktop.

Images: `/mnt/project-files/mobile-app/mockups/{A,B,C}-{phone,desktop}-{light,dark}.png`
(source `shell.html`, `?v=A&t=dark&w=phone`; `node shot.js` renders).

## Data and permissions

- **Feature flag:** no. The new app runs on staging until switch-over, then
  replaces the old one for every community at once.
- **Models:** no domain model changes. New tables for state that lives in process
  memory today: `outbox` (durable queue for DMs, webhooks and event-bus fan-out, so
  nothing is lost when Cloud Run throttles CPU after a response), `oauth_handoff`
  (replaces the in-memory nonce store), and a shared rate-limit counter. The
  per-match seed lock becomes a Postgres advisory lock. A `device_push_token`
  table for APNs/FCM tokens beside the existing web-push subscriptions.
- **Auth:** the app is a first-party public OAuth client of our own authorization
  server (code + PKCE, already built for third-party apps). Roles, tenant and event
  scoping, the archived-event write guard and feature flags are enforced in the
  services as now; the app only hides what the API would refuse.
- **URLs:** `/t/<slug>/e/<event>/…` paths keep their shape so links already sent
  in DMs keep working; `notification_links.py` keeps building them.
- **Other surfaces:**
  - REST: new endpoints for every screen that has none (staying three-layer:
    routers call services). `api/scopes.py` maps each one. OpenAPI client generated
    in CI with a drift check.
  - Live updates: `match_live` / `check_in_live` publish via `NOTIFY`; a
    `GET /api/live` SSE endpoint fans out per community/event.
  - Workers: each `run_worker_loop` worker becomes a Cloud Scheduler-triggered tick
    (the `WORKERS` registry in `scripts/run_worker_tick.py` already runs one tick
    on its own).
  - Discord: buttons move to the HTTP interactions endpoint (signed POSTs, no
    gateway). DMs already go over Discord's REST API. Member update/remove events
    (inbound role sync) need the gateway, so they stay on the bot service.
  - Telegram: switches from polling to Telegram's webhook.
  - racetime: room bot stays on websockets, on the bot service.
  - MCP: already stateless HTTP; moves to Cloud Run unchanged.
- **Hosting:** Cloud Run `api` service (scales to zero or a floor of 1 during
  events), Cloud Run `bots` service (min 1 instance, CPU always on: Discord
  gateway + racetime), Cloud Scheduler for worker ticks, Cloud SQL Postgres (or
  Neon) with a small pool per instance. Static app on Cloud Storage + CDN (or
  Firebase Hosting).
- **Player input:** unchanged; the threat model applies to the API as today.

## PR plan

All PRs are drafts until mergeable. The current app stays in production until the
switch-over, so nothing here may break it.

1. **Spec** committed to `docs/specs/mobile-app.md`.
2. **Backend: stateless state.** Outbox, handoff table, shared rate limiter,
   advisory seed lock, NOTIFY-backed live bus. NiceGUI keeps working on top.
3. **Backend: workers as ticks.** HTTP tick endpoint guarded by Cloud Scheduler's
   OIDC token; leader-only fallback for the current single process.
4. **Backend: Discord interactions over HTTP + Telegram webhook**, bot service
   entry point (gateway + racetime only).
5. **Cloud Run deploy**: Dockerfiles, `api`/`bots` services, Scheduler jobs,
   staging environment against a copy of prod data.
6. **App skeleton** in `web/`: Vite, routing, Studio tokens and base components,
   sign-in via PKCE, generated client, shell (picked option), a11y + contrast CI.
7. **Player screens** (Home, My Schedule, pick a time, check-in, availability,
   profile, notifications) with the REST they need.
8. **Event and public screens** (event info, brackets, Watch, help, changelog).
9. **Crew, volunteer, proctor, check-in desk screens.**
10. **Admin screens** (several PRs, one per admin area).
11. **Platform screens.**
12. **Capacitor iOS/Android shells, native push, app links.**
13. **Switch-over**: point the domain at Cloud Run, remove NiceGUI, `pages/`,
    `theme/`, the browser a11y config for old pages; fold this spec into
    `docs/features/` and delete it.

## Acceptance

- [ ] Every page route and tab in today's app has a screen in the new app that does
      the same things for the same roles (checked against a route/tab inventory).
- [ ] On a phone (360–440px) and on desktop (1440px), every screen uses the picked
      shell and passes the axe + contrast scan in light and dark.
- [ ] A run sheet open on two devices updates on both within 2 seconds of a change
      made on either, with the API running on more than one Cloud Run instance.
- [ ] The API runs on Cloud Run with `min-instances=0` and serves a cold request.
- [ ] Every background worker runs from Cloud Scheduler; none runs in the API process.
- [ ] Killing an API instance mid-request loses no DM or webhook (outbox delivers it).
- [ ] Discord buttons on DMs work with the bot service stopped (HTTP interactions).
- [ ] racetime room bot and inbound Discord role sync run on the bot service.
- [ ] A DM link opens the exact screen in the installed app, and in the browser
      when the app isn't installed.
- [ ] iOS and Android builds install from TestFlight / Play internal testing and
      receive a push.
- [ ] `tests/services/test_webhook_consumer_contract.py` passes unchanged.
- [ ] REST, GraphQL and MCP contracts unchanged except for additions.
- [ ] After switch-over, no NiceGUI code or dependency remains.

## Open questions


## Decisions

- 2026-10-31 — Thomas picked one React web app wrapped for the stores (Capacitor),
  not React Native, Flutter or PWA-only.
- 2026-10-31 — Thomas picked "replace everything, admin last".
- 2026-10-31 — Thomas picked Google Cloud Run.
- 2026-10-31 — Thomas picked workers as scheduled jobs and one small always-on bot
  service for racetime (and the Discord gateway).
- 2026-10-31 — Thomas picked REST with a generated TypeScript client.
- 2026-10-31 — Thomas picked a big-bang switch for SGL27; "admin last" is build
  order only, NiceGUI is retired on switch-over day.
- 2026-10-31 — Thomas picked Postgres LISTEN/NOTIFY + server-sent events for live
  screens.
- 2026-10-31 — Thomas picked the same repo, `web/` folder.
- 2026-10-31 — Thomas picked our own Studio components on Radix.
- 2026-10-31 — Thomas picked bug fixes only in the current (NiceGUI) app while the
  rebuild runs; new features are built once, in the new app.
- 2026-10-31 — Thomas picked shell A: five tabs on phone, left rail on desktop.
- 2026-10-31 — Thomas picked no feature flag.
- 2026-10-31 — Thomas picked one sign-in for every community: the app's grant names
  no community and each REST call names one (`X-Wizzrobe-Community`), checked for
  membership and roles as now.
