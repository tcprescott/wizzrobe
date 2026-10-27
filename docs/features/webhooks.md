# Webhooks

Staff configure outbound HTTP webhooks that fire when selected domain events
occur. When an event is published on the [event bus](event-system.md), the
webhook subscriber POSTs a **signed JSON body** to every enabled webhook whose
`event_types` include that event (or `'*'`).

Scope: **Staff-managed and tenant-wide** — there are no per-user webhooks. All
CRUD is staff-gated; management lives in a single admin tab.

Source: [`application/services/webhook_service.py`](../../application/services/webhook_service.py),
models `Webhook` / `WebhookDelivery` in [`models/webhook.py`](../../models/webhook.py),
REST in [`api/routers/webhooks.py`](../../api/routers/webhooks.py),
UI in [`pages/admin_tabs/admin_webhooks.py`](../../pages/admin_tabs/admin_webhooks.py).

## Request format

Each delivery is `POST url` with a JSON body. Keys are serialized sorted
(`json.dumps(..., sort_keys=True)`) so the byte sequence is deterministic and the
signature is reproducible:

```json
{
  "event_type": "match.created",
  "occurred_at": "2026-07-03T23:25:33.739+00:00",
  "tenant_id": 2,
  "event": { "id": 3, "slug": "sgl26", "name": "SG Live 2026" },
  "actor_id": 12,
  "actor_username": "alice",
  "data": { "match_id": 5, "tournament_id": 2, "player_ids": [1, 2] }
}
```

`tenant_id` is the [tenant](multitenancy.md) the event occurred in, snapshotted in
`Event.create()` from the ambient context (`null` for platform-level events). A
webhook only ever receives events for its own tenant — `Webhook` rows are
tenant-scoped and the subscriber delivers to just that tenant's — so the field is
a stable label, not a filter the receiver must apply.

### Event filter

`Webhook.event_ids` limits a webhook to some of the community's
[events](events.md): `NULL` (every new webhook, and every existing one but
"SG Schedule Update", which migration 143 pinned to its community's default
event) sends every event's messages, including events added later. A message
carries `Event.event_id` (see [event-system.md](event-system.md#which-event-a-message-is-from));
`deliver_event` skips a webhook whose list doesn't hold it. A community-wide
message (`tenant.*`, `oauth_grant.*`) has no event and always passes.
`WebhookService` refuses an empty list, an unknown event and an archived one.

The body's `event` is `{id, slug, name}` of that event, `null` for a
community-wide message. `deliver_event` resolves it once per message
(`WebhookService.event_ref`, from the cached `EventService.ref`) and hands it to
`to_wire`; a deleted draft is gone by then, so `event.deleted` falls back to the
slug and name in its own payload. It was added on 2026-10-26, after SG Live 2026,
with Thomas's go-ahead to break the frozen envelope in
`tests/services/test_webhook_consumer_contract.py`. Personal webhooks' bodies
don't carry it yet.

Headers (built by `WebhookService.build_delivery_headers` — the single source of
truth the in-app reference also renders):

| Header | Meaning |
|---|---|
| `Content-Type` | Always `application/json` |
| `User-Agent` | Identifies the sender (`wizzrobe-webhook`) |
| `X-Wizzrobe-Event` | The event name |
| `X-Wizzrobe-Delivery` | Unique delivery UUID |
| `X-Wizzrobe-Timestamp` | Unix seconds; part of the signed string (replay defense) |
| `X-Wizzrobe-Signature` | `sha256=<hex>` — HMAC-SHA256 of `"{timestamp}.{body}"` using the webhook secret |

> Staff can view this format live in the app: **Admin → Settings → Integrations → Webhooks → "Webhook payload
> & event reference"**, generated from the delivery code so it never drifts.

### Verifying the signature (receiver side)

```python
import hmac, hashlib
signed = f"{request.headers['X-Wizzrobe-Timestamp']}.{raw_body}".encode()
expected = "sha256=" + hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
assert hmac.compare_digest(expected, request.headers['X-Wizzrobe-Signature'])
```

Sign the **raw body bytes** exactly as received — the app signs the exact bytes it
sends. Reject requests whose timestamp is too old to defend against replay.

## Delivery, retries, logging

- Runs on the event dispatch worker (off the request path). `deliver_event`
  picks the tenant's active webhooks and enqueues each POST as its own dispatch
  item, re-wrapped in the webhook's `tenant_scope`. Each POST uses
  `httpx.AsyncClient` with a 10s timeout and `follow_redirects=False`.
- Bounded retry: up to `MAX_ATTEMPTS` (3) with exponential backoff (`2**attempt`
  seconds) on any non-2xx or transport error.
- Every delivery writes one `WebhookDelivery` row (final status, attempt count,
  success, error, `delivered_at`) for observability — visible in the admin tab's
  "Recent deliveries" view. The webhook list also shows a per-row health chip
  ("N of M failed" / "M delivered") from the last 24 hours (`recent_health`).

## Security

- **Secret**: `secrets.token_urlsafe(32)`, stored plaintext because it must be
  reproducible to sign each request. Shown **once** on create/regenerate; never
  returned by list/GET and never logged.
- **SSRF**: on create/update the URL must be `https://`; in production the host is
  resolved (`application/utils/ssrf.py`) and rejected if any address is private,
  loopback, link-local (incl. `169.254.169.254` cloud metadata), reserved,
  multicast or unspecified. In production the check runs **again before every
  delivery attempt**, and the POST connects to the address that passed
  (`pin_public_url`: the IP in the URL, the hostname as `Host` and as the TLS
  server name), so a rebinding DNS answer can't pass the check and then point
  the request inward. A blocked delivery is logged as a failed
  `WebhookDelivery`. Redirects are never followed.
  Outside production, `http://` and localhost are allowed so a developer can
  point at a local receiver.

## Managing webhooks

- **Admin UI**: Admin → Settings → Integrations → **Webhooks** (Staff only). One
  full-width row per webhook (`.wiz-webhook-row`): active dot, name (opens Edit)
  and URL, subscribed events grouped by area and wrapped, the 24h health chip,
  a **Deliveries** button and a ⋮ with Edit, Regenerate secret, Pause/Resume and
  Delete. A narrow list stacks the same row (a container query, not the
  viewport). The toolbar is **Add webhook** plus an Actions menu holding the
  payload & event reference dialog and Refresh.
- **Event filter** (Thomas's layout A): the dialog's **From events** is a
  radio, *All events* (`NULL`) or *Only these events* with a picker of live and
  draft events (an archived one only when the webhook already has it, which
  `WebhookService` lets it keep). Each row names its events under the URL.
  The event-type multiselect is labelled **Updates** so the two don't clash.
- **REST**: `event_ids` on create and update sets the event filter (above).
- **REST**: `/api/webhooks` — `GET` (staff), `POST` (staff-write, returns the
  secret once), `GET/PUT/DELETE /{id}`, `POST /{id}/regenerate-secret`,
  `GET /{id}/deliveries`. See [rest-api.md](../reference/rest-api.md).

## Events available

Everything in [`EventType.ALL`](../../application/events/event_types.py) — the
`match.*`, `crew.*`, `volunteer.*`, `bracket.*`, `race_room.*`, `sg_sync.*`,
`discord_event.*`, `async_qualifier.*`, `tenant.member_*` / `tenant.join_*` and
`tournament.*` (enrolment, prize pool, payouts), `check_in_event.synced` and
`check_in_entrant.*` families, listed per publisher in
[event-system.md](event-system.md). Select `*` to receive all of them. The one
member no webhook can receive is `service_health.alert`: it is platform-level
(no tenant), and delivery is tenant-scoped.

## Personal webhooks (members)

Behind `FeatureFlag.PERSONAL_WEBHOOKS` (off by default), any member can add up
to three webhooks of their own on **Profile → Webhooks**
([`personal_webhooks_section.py`](../../pages/home_tabs/personal_webhooks_section.py)).
They are a separate model (`PersonalWebhook`) and service
([`personal_webhook_service.py`](../../application/services/personal_webhook_service.py)),
and share nothing with staff webhooks except the header/signature scheme, so the
staff contract above is untouched.

- **Catalogue only.** `EVENTS` in the service lists what a member can subscribe
  to, grouped into `AREAS` (matches, crew, volunteer shifts, brackets,
  qualifiers, race rooms; an area whose feature is off is hidden). Comments,
  review flags, staff-side presets and everything else stay staff-only.
  `match.cancelled`/`match.deleted` are deliberately absent: both remove the
  match before delivery could tell whose it was.
- **Checked per delivery.** `deliver_event` is subscribed with the catalogue as
  its `event_types` filter. For each active subscriber, `view_for` re-checks the
  owner is an active member (membership or a community role), loads the row the
  event is about inside the event's tenant, and decides `about_me` (player or
  approved crew on the match, the crew/shift/run owner, an entrant in the
  bracket). Not about the owner → dropped if the webhook has **Only things about
  me** on, or the event is `mine_only` (crew, shifts, qualifier runs,
  reschedule requests).
- **Rebuilt payloads.** The view copies an allowlist of fields; the published
  event's own payload is never forwarded. JSON body: `event_type`,
  `occurred_at`, `community` (slug), `about_me`, `link`, `data`. Discord
  destinations (`discord.com/api/webhooks/…`) get an embed with
  `allowed_mentions: {parse: []}` and escaped markdown, times as `<t:…:F>`.
- **Limits.** https only and SSRF-checked at save and at send (production),
  no credentials in the URL, 20 saves/hour, 3 tests per 10 minutes, 60
  deliveries/minute per webhook, 2 attempts (4xx other than 429 not retried).
  20 failures in a row, or a Discord 401/404, pauses the webhook with a reason
  the card shows. The stored URL never goes back to the page (`masked_url`).
- **Audit:** `personal_webhook.created/updated/deleted/secret_regenerated`.
  No REST or MCP surface yet.

## Tests

[`tests/services/test_webhook_service.py`](../../tests/services/test_webhook_service.py)
(CRUD/auth/validation, HMAC signing, delivery logging, retry-then-give-up) and
[`tests/api/test_webhooks.py`](../../tests/api/test_webhooks.py) (endpoint auth,
CRUD, secret-once), and
[`tests/services/test_personal_webhook_service.py`](../../tests/services/test_personal_webhook_service.py)
(who can create, per-owner visibility, allowlisted payloads, Discord body, pausing).
