# Device Notifications (Web Push)

Users turn native push notifications on per device from **Profile → Notifications →
Where it goes**: one switch for the browser the page is open in, and a row with
Remove for every other subscribed device. Every Discord DM the app sends — match scheduled/rescheduled,
acknowledgment requests, crew signups, seed URLs, watcher updates, volunteer
reminders — is mirrored to that user's subscribed devices, so iOS and Android users
get notified without the Discord app installed.

Devices and Discord are **independent channels**. `User.dm_notifications` is the
**Discord DMs** switch only, and `send_dm` enforces it itself, *after* mirroring:
a user with Discord off still gets every notification on their subscribed devices.
Senders no longer check the flag (a sender that skipped opted-out users would
silence their devices too); they only need a `discord_id`. With Discord off and no
devices the profile shows a warning that nothing can reach them.

The feature is **off until VAPID keys are configured** (see Setup); without them
the settings section hides itself and every send is a silent no-op.

## One payload, two rendering paths

The encrypted payload is JSON in WebKit's **Declarative Web Push** shape:

```json
{
  "web_push": 8030,
  "notification": {
    "title": "Wizzrobe",
    "body": "Your match has been scheduled for 2026-07-04 18:00 EST",
    "navigate": "https://wizzrobe.example.com/t/sglive/home/player"
  }
}
```

`navigate` is the DM's [`DMLink`](discord.md#calls-to-action) URL, so tapping
the notification opens the same control the Discord link button does. A DM with
no link falls back to `BASE_URL/`.

`"web_push": 8030` is the magic member that opts the message into declarative
parsing (Safari/iOS 18.4+ renders it with no service worker); `title` and
`navigate` are required. Browsers without declarative support fire the classic
`push` event and [`static/sw.js`](../../static/sw.js) renders the *same* JSON via
`showNotification()`, so there is one payload format for every platform.

The one platform caveat that changes what a user must do: on **iOS/iPadOS the app
must be added to the Home Screen** — Safari exposes no push API in a plain tab.

## Architecture

```
DiscordService.send_dm  ──(mirror, never raises)──▶  WebPushService.mirror_dm
                                                            │ lookup subscriptions by discord_id
                                                            ▼
                                              encrypt (RFC 8291 aes128gcm)
                                              + VAPID JWT (RFC 8292)
                                                            │ httpx POST per device
                                                            ▼
                                              push service ──▶ device notification
```

- **Send path.** `DiscordService.send_dm` is the chokepoint every notification
  fan-out flows through (match lifecycle, crew, watchers, volunteers, seeds), so
  mirroring there gives device notifications exactly the coverage DMs have, future
  notification types included, with no per-call-site wiring. The mirror is
  **enqueued fire-and-forget onto the event dispatch worker**, so neither the
  serial `discord_queue` nor a UI handler awaiting `send_dm` inline ever waits on
  push-service round-trips. It fires *before* the bot-readiness checks (pushes
  still go out when the bot is down), never raises, and creates no coroutine at
  all while VAPID is unconfigured. Two corollaries: the Discord switch
  (`User.dm_notifications`) is checked inside `send_dm` after the mirror, so a
  user with Discord off gets `(True, DM_DISCORD_OFF)` and their devices still
  fire; and a `(False, ...)` return means the *Discord* send failed — devices may
  already have been notified, so re-sending can double-notify them.
- **Mock mode sends nothing.** `MockDiscordService.send_dm` deliberately skips the
  mirror so [`MOCK_DISCORD`](discord.md#mock-mode) keeps its
  no-external-side-effects guarantee: a dev with a prod DB snapshot and prod VAPID
  keys must not push to real phones.
- **Protocol.** Implemented natively on `cryptography` (not `pywebpush`, which is
  blocking/`requests`-based) in
  [`application/utils/web_push.py`](../../application/utils/web_push.py):
  `aes128gcm` encryption per RFC 8291 and ES256 VAPID authorization per RFC 8292.
  Delivery uses a shared `httpx.AsyncClient` (keep-alive, closed in the app
  lifespan) with devices delivered concurrently, VAPID headers cached per
  push-service origin for the token lifetime, and encryption run in a worker
  thread off the event loop.
- **Subscription lifecycle.** Push services answer `404`/`410` for dead
  subscriptions; `WebPushService` prunes the row on either status. Users can
  also remove devices from the settings UI.
- **Rotation.** A push service can retire a subscription on its own (key
  rotation, a long idle period, a browser update) and fire
  `pushsubscriptionchange` in the service worker. `static/sw.js` re-subscribes
  and POSTs the reissued endpoint to `POST /api/web-push/rotate`, which moves
  the *existing* row onto it — so the device keeps its identity and history
  instead of going quiet until the user notices. See
  [Authenticating a rotation](#authenticating-a-rotation).
- **Subscribing (client).** Browser permission prompts must happen inside the
  click gesture (Safari enforces this), so the enable/disable buttons in
  [`pages/home_tabs/web_push_section.py`](../../pages/home_tabs/web_push_section.py)
  run [`static/js/web-push.js`](../../static/js/web-push.js) client-side via
  NiceGUI `js_handler` and report back through `emitEvent` → `ui.on`. The
  helper prefers `window.pushManager` (declarative, no service worker) and
  falls back to the service-worker registration's `pushManager`. On iOS
  without home-screen install it reports `ios_needs_install`, which the UI
  turns into instructions. Only the standalone Home Screen app may touch the
  PushManager on iOS: a normal Safari or Chrome tab can still hand one back,
  and subscribing through it takes the whole tab down with no JS error, so
  nothing reaches Sentry.
- **The service worker caches nothing.** `static/sw.js` has a pass-through
  `fetch` handler only because Android/Chrome offer "Install app" solely when a
  registered worker has one; with no cache there is no stale-asset class of bug.

### Authenticating a rotation

`pushsubscriptionchange` wakes the worker with no page, no session and no user
gesture, so the rotation request cannot present a bearer token or a logged-in
actor the way subscribing does. It proves possession of the **retired
subscription's `auth` secret** instead — one of the two RFC 8291 client keys,
which never leaves the browser except over TLS and is never logged or rendered.
A mismatch and an unknown endpoint return the *same* 404, so probing cannot
distinguish them. Without that check the endpoint would be an unauthenticated
"redirect this user's notifications to an endpoint of my choosing" primitive for
anyone who learned a stored endpoint URL.

The worker cannot rely on the event to supply those values — browsers have
shipped `pushsubscriptionchange` with both `oldSubscription` and
`newSubscription` null. So [`static/js/web-push-common.js`](../../static/js/web-push-common.js)
keeps the device's endpoint, `auth` secret and application server key in
IndexedDB, written by the page at subscribe time and read by the worker much
later; the event's own values are used when present. Missing both records, the
worker bails rather than registering a device the server cannot attribute.
Disabling notifications clears the record, so a later rotation cannot resurrect
a device the user just turned off.

### The endpoint is an SSRF vector, checked twice

A stored endpoint is a URL the *browser* supplied and the *server* later POSTs
to, so it is dereferenced with the app's own network position. `subscribe` and
`rotate` both require `https`, cap the length, and run the shared
[`ensure_public_host`](../../application/utils/ssrf.py) guard — the same one the
webhook targets use — which rejects private, loopback, link-local, reserved,
multicast and unspecified addresses, including the `169.254.169.254` metadata
endpoint.

`_deliver_one` runs it **again** at send time. The subscribe-time check resolves
DNS once, but the delivery POST re-resolves it, so a host repointed at an
internal address afterwards (slow DNS rebinding) would otherwise be reached on a
stale verdict. A blocked delivery is *skipped, not pruned*: the host may resolve
publicly again on the next send, and dropping a real device's subscription over
one transient answer is the worse failure.

Both checks are production-only, matching the webhook guard, so a development
setup can still point at a local push mock.

Source: model `WebPushSubscription` in [`models/user.py`](../../models/user.py)
(user × device: unique `endpoint`, `p256dh`, `auth`, `user_agent`, `created_at`, `last_used_at`; the repository's
endpoint upsert re-binds a device to the latest user),
[`web_push_service.py`](../../application/services/web_push_service.py),
[`application/utils/web_push.py`](../../application/utils/web_push.py),
[`pages/home_tabs/web_push_section.py`](../../pages/home_tabs/web_push_section.py),
[`static/js/web-push.js`](../../static/js/web-push.js),
[`static/js/web-push-common.js`](../../static/js/web-push-common.js),
[`static/sw.js`](../../static/sw.js),
[`api/routers/web_push.py`](../../api/routers/web_push.py),
[`scripts/generate_vapid_keys.py`](../../scripts/generate_vapid_keys.py).

## Setup

1. Generate a keypair once: `poetry run python scripts/generate_vapid_keys.py`
2. Set in the environment (see [deployment.md](../deployment.md)):
   - `VAPID_PRIVATE_KEY` — base64url raw 32-byte P-256 scalar. **Keep secret
     and stable**: rotating it silently invalidates every existing
     subscription.
   - `VAPID_SUBJECT` — `mailto:` or `https:` contact sent to push services;
     defaults to `BASE_URL` when that is https.
3. Users enable notifications per device from their profile page.

Web Push requires a secure context: `https://` in production (`localhost` is
exempt for development).

## Audit

`web_push.subscribed` / `web_push.unsubscribed` / `web_push.rotated`
(`AuditActions`), actor = the user (for a rotation, the owner of the row being
moved — there is no session actor), details = subscription id + endpoint host
(full endpoints are capability URLs and are not logged).

## Limitations

- **A DM with no `DMLink` opens the site root** — informational DMs (checked
  in, cancelled, state changed) carry no link, so their push lands on
  `BASE_URL/`.
- **Rotation needs the stored IndexedDB record**: a device that subscribed
  before this shipped, or whose site data was cleared, has no record — and if
  the browser also fires `pushsubscriptionchange` with null subscriptions, the
  worker cannot authenticate the rotation and the row 410-prunes as before.
  Re-enabling from the profile page writes the record.
- **Interactive DM buttons don't translate**: pushes mirror the DM text only;
  acknowledge/signup actions still happen in Discord or the web UI.
- **Markdown stripping covers `**` only** — the only token the DM templates
  emit. New template markdown needs `_MARKDOWN_TOKENS` updated (blindly
  stripping more would corrupt usernames/URLs containing `__` or backticks).

## Tests

[`tests/test_web_push_protocol.py`](../../tests/test_web_push_protocol.py)
(RFC 8291 Appendix A byte-exact vector, round-trip decrypt, VAPID JWT
signature verification) and
[`tests/services/test_web_push_service.py`](../../tests/services/test_web_push_service.py)
(config gating, subscription CRUD/auth/audit, declarative payload contents,
410 pruning, rotation incl. the auth-secret requirement, mirror never-raises),
plus [`tests/api/test_web_push.py`](../../tests/api/test_web_push.py) for the
unauthenticated rotation endpoint.
