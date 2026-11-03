# Patreon patron badges

A community connects its Patreon campaign; members link their Patreon account;
active patrons get a badge in that community. Wizzrobe never handles money and
never sees a pledge amount. Behind `FeatureFlag.PATREON` (off by default).

Spec while it's being built: [docs/specs/patreon-patron-badges.md](../specs/patreon-patron-badges.md).

## The entitlement

`PatronService().get_patron_status(user, tenant_id=None)` returns a
`PatronStatus` (`is_patron`, `tier_ids`, `badge_label`, `last_synced`,
`badge_hidden`). **Every perk gates on this call**, never on Patreon tables or
Patreon itself. With the flag off it answers "not a patron" rather than
raising. `badge_hidden` only hides the badge from other people; perks still
apply. `visible_badges()` is the bulk form for member lists.

## Sign-ins

One platform Patreon OAuth app (`PATREON_CLIENT_ID` / `_SECRET`, see
[deployment](../deployment.md)) serves both:

| Who | Scopes | Starts at | Lands on |
|---|---|---|---|
| Staff, as the campaign's creator | `identity campaigns campaigns.members w:campaigns.webhook` | `POST /api/settings/integrations/patreon/connect` | `/admin/settings/integrations?patreon=…` |
| Any member | `identity identity.memberships` | `POST /api/me/patreon/link` | `/me/accounts?patreon=…` |

Both return an `authorize_url`; Patreon sends the browser to
`GET /api/patreon/callback`, which has no session. A `PatreonOAuthState` row
(SHA-256 of the state, single use, ten minutes) says who started it, in which
community, and for what. Outcomes: `connected`, `linked`, `taken` (that Patreon
account is linked to someone else), `cancelled`, `expired`, `failed`.

The creator's tokens are sealed per community and refreshed three days before
they lapse, under a `shared_lock` per community (Patreon rotates the refresh
token). A refused refresh sets `last_sync_error` asking staff to reconnect. A
member's token is used once (identity plus memberships) and dropped; only the
Patreon user id and name are kept, one Patreon account per Wizzrobe account.

## Keeping status current

- **Webhook** (`POST /api/patreon-webhooks/{token}`, registered on connect for
  `members:create|update|delete`). `X-Patreon-Signature` is HMAC-MD5 of the raw
  body under the webhook secret; a bad or missing signature is a 403 before the
  body is read. The URL token picks the community, never the payload. Deliveries
  for people who haven't linked, or another campaign, are ignored and stored
  nowhere. With the flag off, acknowledged and ignored.
- **Sync worker** (`patreon_sync`, every 5 minutes): refreshes tokens, and runs a
  full pass when one is due (every 6 hours), queued by staff's Sync now, or
  queued by a link whose membership Patreon didn't show at sign-in. Pages the
  campaign's members 1,000 at a time, waits out 429s, and corrects every linked
  member's row; rows for people who left the community go.

Every badge turning on or off writes `patreon.badge_granted` /
`patreon.badge_revoked` (source `webhook`, `sync`, `link` or `unlink`).

## Cleanup

- Disconnect: removes the webhook on Patreon (best effort), the tokens, tier
  labels and every membership row here. One `patreon.disconnected` audit row with
  the count.
- Unlink: forgets the Patreon id and every membership row in every community, a
  revoke row in each community where the badge was on, and `patreon.unlinked`.

## Surfaces

REST as above plus `GET|PUT /api/settings/integrations/patreon[/tiers]`,
`POST …/sync`, `DELETE …`, `GET|PATCH|DELETE /api/me/patreon`. Admin → Members
rows carry `patron_badge`; `GET /api/users/{id}/details` and MCP
`get_member_details` carry a `patron` block. Local dev: `MOCK_PATREON=1`.
