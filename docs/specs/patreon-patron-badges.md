# Patreon patron badges (phase 1)

Status: Draft · Size: full · PR: — · Requested: 2026-11-03, Thomas Prescott

## Problem

Communities fund themselves through Patreon, but Wizzrobe can't tell who their
patrons are. Thomas wants a community to connect its Patreon campaign, members to
link their Patreon account, and patrons to get a "Patron" badge in that
community. Later features (patron-only Triforce texts) need one place to ask
"is this person a patron here?". Wizzrobe never handles money.

## Scope

In:
- Staff connect the community's Patreon campaign by signing in as the creator
  (OAuth2, API v2, scopes `identity campaigns campaigns.members
  w:campaigns.webhook`). Staff can disconnect.
- Wizzrobe registers a Patreon webhook on the campaign (`members:create`,
  `members:update`, `members:delete`) and checks `X-Patreon-Signature` on every
  delivery. Patreon signs with HMAC-MD5 of the raw body under the webhook
  secret; we compare in constant time and reject anything else with no change.
- Members link their Patreon account once (OAuth2, scopes `identity
  identity.memberships`). The link is global; patron status is per community.
- `patron_status == "active_patron"` in a community's campaign turns the badge
  on there. Declined and former patrons lose it.
- Optional tier → badge label mapping per community (default label "Patron").
  When someone holds several mapped tiers, the first in the staff's order wins.
- A reconciliation worker pages each connected campaign's members every 6 hours
  and fixes drift; it also refreshes the creator token before it lapses.
- One entitlement call for every later feature:
  `PatronService.get_patron_status(user, *, tenant_id=None) -> PatronStatus`
  (`is_patron`, `tier_ids`, `badge_label`, `last_synced`). Nothing outside the
  Patreon modules reads Patreon tables.
- Badge beside the member's name on Admin → Members, the member sheet and
  the person's own Me page (exact look per the mockup pick). Run sheets,
  brackets and Watch come later.
- MCP `get_member_details` and REST `GET /api/users/{id}/details` gain a
  read-only `patron` block.
- New feature flag `PATREON` ("Community" group), both halves: entry surfaces
  hidden, owning services `@requires_feature`. `get_patron_status` is the
  soft-integration carve-out: with the flag off it returns "not a patron"
  instead of raising, so a later caller never breaks.
- Audit rows: `patreon.connected`, `patreon.disconnected`, `patreon.linked`,
  `patreon.unlinked`, `patreon.badge_granted`, `patreon.badge_revoked`, plus
  matching `EventType`s. Disconnect and unlink write one summary row with the
  count of badges removed rather than one per person.

Out:
- Triforce text submission, paid bidding, credits, payouts.
- Discord roles for patrons.
- Pledge amounts anywhere: we request only `patron_status` and the entitled
  tier ids/titles from Patreon (sparse fieldsets), so amounts never reach us.
- Per-community Patreon OAuth apps (one platform app serves every community).
- Any change to the NiceGUI app.

## Flow

1. Staff open the Patreon card under Settings → Integrations, tap Connect, sign
   in to Patreon as the creator and approve. Patreon returns to
   `/api/patreon/callback`, which stores the sealed tokens, picks the creator's
   campaign (asks which one if there are several), registers the webhook, runs
   a first sync in the background, and redirects back to the card.
2. Staff optionally map tiers to labels ("Hero tier" → "Hero") and reorder them.
3. A member opens Me → Accounts, taps Link Patreon, signs in and approves. We
   keep their Patreon user id (and the name shown as "Linked as …" to them
   only), read their memberships once, then discard the token.
4. Status changes arrive by webhook. The community is resolved from the
   webhook URL's row, never from the payload; the member is matched by Patreon
   user id. Deliveries for people who haven't linked are ignored and stored
   nowhere.
5. The 6-hourly worker (and a staff "Sync now") pages campaign members
   (`page[count]=1000`, cursor pagination), corrects any badge that drifted,
   and backs off on HTTP 429 using `Retry-After`.
6. Disconnect deletes the webhook on Patreon (best effort), the tokens, the
   tier map and every membership row for that community. Unlink deletes the
   member's Patreon id and every membership row for them in every community.

## Mockup pick

Pending. Mockups for the Integrations card, Me → Accounts row, tier mapping
dialog and the badge chip, phone and desktop, light and dark, at
`/mnt/project-files/patreon/mockups/`.

## Data and permissions

Feature flag: `PATREON`, off by default.

New models (community-owned: `tenant_id`, no `event_id`):
- `PatreonCampaign` (one per tenant): `campaign_id`, `campaign_name`,
  `access_token`, `refresh_token`, `webhook_secret` (each sealed with
  `secret_box`, purpose `patreon`, context tenant id), `token_expires_at`,
  `webhook_id`, `webhook_token` (opaque URL id), `connected_by`,
  `connected_at`, `last_synced_at`, `last_sync_error`.
- `PatreonTierBadge`: `tier_id`, `tier_title`, `badge_label`, `sort`; unique
  (tenant, tier_id).
- `PatreonMembership`: `user`, `patreon_member_id`, `patron_status`,
  `tier_ids` (JSON list), `synced_at`; unique (tenant, user). Only linked users
  get a row.

Global, on `User` (through `IdentityLinkService`, like Twitch and racetime):
`patreon_user_id` (unique, nullable), `patreon_username`, `patreon_linked_at`,
`patreon_badge_hidden` (a "Show my badge" switch on Me → Accounts; hiding
removes the chip everywhere but leaves `get_patron_status` and the staff
member sheet unchanged).

Modules: `application/utils/clients/patreon_client.py` (async httpx, OAuth,
identity, campaigns, members, webhooks) + `application/utils/mocks/mock_patreon.py`
(`PATREON_MOCK`), `PatreonRepository`, `PatreonCampaignService`,
`PatreonLinkService`, `PatronService`, `patreon_sync_worker.py` (registered in
`WORKERS`, `run_worker_loop`, `for_each_tenant_scoped`, skips a tenant with the
flag off). Refreshes take a `shared_lock` per community, since Patreon rotates
the refresh token.

Routes: `GET/DELETE /api/integration-settings/patreon`, `POST .../connect`
(returns the authorize URL), `PUT .../tiers`, `POST .../sync`;
`GET/DELETE /api/me/patreon`, `POST /api/me/patreon/link`,
`PATCH /api/me/patreon`; `GET /api/patreon/callback`;
`POST /api/patreon-webhooks/{token}` (no auth, signature only, outside the
OpenAPI schema like the ticket webhooks).

Permissions: STAFF connect, disconnect, map tiers and sync; other staff of
another community can't see or touch it (community wall). Any member links and
unlinks their own account. No token or webhook secret appears in any response,
log, audit row or MCP output. Staff see a member's status on the member sheet.
The OAuth `state` is server-issued, single-use, bound to the user, tenant and
purpose (creator vs member).

Env vars: `PATREON_CLIENT_ID`, `PATREON_CLIENT_SECRET`, `PATREON_REDIRECT_URI`
(defaults to `<base>/api/patreon/callback`), `PATREON_MOCK`. No new Python
dependencies, so pip-audit is unaffected. A service health probe joins
`_PROBES`.

## PR plan

1. This spec (draft until signed off).
2. Backend: models + migration, client + mock, repository, services, webhook,
   worker, REST, MCP `patron` block, flag, audit/event types, seed rows, tests.
3. UI after the mockup pick: Integrations card, tier dialog, Me → Accounts,
   badge chip; changelog file; `docs/features/patreon.md`; delete this spec.

## Acceptance

- [ ] With `PATREON` off, no Patreon card, route or worker runs for that
      community, service calls raise not-found, and `get_patron_status` says
      not a patron.
- [ ] Staff connect a campaign; a non-staff member and another community's
      staff get refused (test).
- [ ] Tokens and the webhook secret are sealed at rest and never appear in a
      response, log, audit row or MCP output (test).
- [ ] An expired creator token is refreshed automatically; two refreshes at
      once don't spend the refresh token twice (test).
- [ ] A webhook with a valid signature updates the badge; a bad or missing
      signature returns 403 and changes nothing (test).
- [ ] A linked member goes active → declined → former and the badge turns on,
      then off, with grant/revoke audit rows (test).
- [ ] Reconciliation fixes a badge that a missed webhook left wrong, across
      several pages, and waits out a 429 (test).
- [ ] Disconnect removes the webhook, tokens, tier map and badges; unlink
      removes the Patreon id and badges in every community (test).
- [ ] No pledge amount is requested from Patreon, stored or shown.
- [ ] `get_member_details` (MCP + REST) shows `patron`.
- [ ] On a phone and on desktop, light and dark, the Integrations card, Me →
      Accounts row and badge match the picked mockup and pass `npm run a11y`.
- [ ] CI green, guardrails and pip-audit included.

## Open questions

- Risk to check in the build: Patreon may only return memberships to the OAuth
  client owner's campaign from `identity.memberships`. If so, linking queues a
  targeted reconcile for the communities the member belongs to instead, and
  the badge shows within a minute or two rather than instantly.

## Decisions

- 2026-11-03 — Thomas picked new app only: screens in `web/` (Settings →
  Integrations card, Me → Accounts); NiceGUI untouched.
- 2026-11-03 — Thomas picked drop after linking: only the member's Patreon
  user id is kept; their OAuth token is used once and discarded.
- 2026-11-03 — Thomas picked everyone sees the badge, member can hide it.
- 2026-11-03 — Thomas picked members list + profiles for phase 1.
- 2026-11-03 — Thomas: Patreon phase 1 is badges only; Wizzrobe never handles
  money; future perks gate on `get_patron_status`.
