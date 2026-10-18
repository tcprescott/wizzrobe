# Third-party apps (OAuth 2 authorization server)

_Backend reference for the `/oauth` authorization server that lets people sign in to outside apps with their Wizzrobe account. Part of the [documentation index](../README.md). The design and work plan live outside the repo; this page describes what the code does._

**Status:** backend only, behind `FeatureFlag.THIRD_PARTY_APPS` (ships dark). The consent screen (`/oauth/consent`), the developer-apps and connected-apps views, per-route scope enforcement on `/api`, per-app rate limits and CORS are later work items. Until the consent page exists, `/oauth/authorize` parks the request and redirects to a path nothing serves yet.

## Shape

| Piece | Where |
|---|---|
| Models | [`models/oauth_app.py`](../../models/oauth_app.py): `OAuthApp` (global, owned by a user), `OAuthAppCode` (single-use, 5 min, bound to the picked community). Grants are `ApiToken` rows with `origin='app'`, `oauth_app`, `tenant` = picked community, `scope`, `previous_refresh_hash` |
| Scope catalogue | [`application/api_scopes.py`](../../application/api_scopes.py) (`ApiScope`, shared with the REST scope check) |
| Registration | [`OAuthAppService`](../../application/services/oauth_app_service.py) |
| Authorization server | [`OAuthGrantService`](../../application/services/oauth_grant_service.py) |
| HTTP surface | [`api/oauth_server.py`](../../api/oauth_server.py), mounted from `main.py` before NiceGUI |
| REST check | `api/dependencies.py::_ensure_app_grant_usable`, called from `resolve_token` for app grants |

A separate issuer from the MCP server's (`{BASE_URL}/oauth` vs `{BASE_URL}`), and a separate token prefix (`wizzrobe_app_` / `wizzrobe_appref_`). The MCP endpoint already refuses any token whose origin isn't `oauth`, and `/api` refuses MCP tokens, so neither credential crosses over.

Hand-rolled rather than built on the MCP SDK's authorization server: the SDK compares client secrets in plaintext, and we only store hashes; it also has no idea of a community or of area scopes.

## Endpoints

| Endpoint | Notes |
|---|---|
| `GET /.well-known/oauth-authorization-server/oauth` | RFC 8414 metadata (path-insertion form, so the MCP root document is untouched). Advertises `S256` only, `client_secret_basic`/`client_secret_post`/`none`, the eight scopes, and `authorization_response_iss_parameter_supported` |
| `GET /oauth/authorize` | Unknown/disabled client or an unregistered `redirect_uri` → a plain 400 page, never a redirect. Anything else wrong (no PKCE, `plain`, `response_type` ≠ `code`, unknown or unregistered scope) → redirect with `error`, `state`, `iss`. Success → `302 /oauth/consent?request=<id>` (in-process, 15 min) |
| `POST /oauth/token` | `authorization_code` and `refresh_token` only (no client-credentials, no password, no implicit). Confidential apps authenticate with Basic or form secret (not both); public apps send `client_id` only and are refused if they send a secret. Errors are RFC 6749 JSON with `Cache-Control: no-store` |
| `POST /oauth/revoke` | RFC 7009. Either half revokes the grant; unknown tokens answer 200 |

All four share the `/api` rate limiter.

## Rules worth knowing

- **Scopes only narrow.** A grant does what its scopes cover *and* what the person's roles in that community allow. A grant with no write scope is stamped `read_only`, so every write route already refuses it before W2's per-route scope check lands.
- **Consent** (`consent_view` / `approve` / `deny`): only communities the person belongs to, active, with the flag on. `staff:*` are offered only to someone holding an admin-side role there (`can_view_admin`, or any role besides Volunteer). The grant carries exactly the ticked scopes, which must sit inside the offer.
- **One live grant per person × app × community.** Approving again replaces the old grant.
- **Rotation and reuse.** Access tokens last 1 h, refresh tokens 30 days sliding and rotate on every use. Presenting a rotated-away refresh token revokes the grant (`oauth_grant.refresh_reused`). A replayed authorization code revokes the grant it produced.
- **Re-checked at each exchange and each API call:** flag on, community active, account active, still a member, app not disabled and owner active. Flag off answers 404 at `/api` and `invalid_grant` at the token endpoint, without revoking anything.
- **Registration:** anyone signed in, 10 apps each. Name ≤ 60, description ≤ 300, plain text with control and bidi characters stripped; homepage https only. Redirects follow the MCP rules (`is_allowed_redirect_uri`), max 10, and a confidential app may only use https or loopback http. Secret rotation keeps the old secret for 24 h. Deleting an app revokes every grant; a platform admin's disable stops them reversibly.
- **Revocation paths that already work:** the person (profile token list, since grants are `ApiToken` rows), the app (`/oauth/revoke`), member removal (`revoke_for_user_in_tenant`), app delete, platform disable.

## Audit and events

Audit: `oauth_app.created/updated/secret_rotated/deleted/disabled/enabled`, `oauth_grant.created/revoked/refresh_reused`. Events: `oauth_grant.created` and `oauth_grant.revoked` (tenant-scoped, payload names the app and scope). App registration is global and stays audit-only.

## Dev seed

[`scripts/seed_oauth_apps.py`](../../scripts/seed_oauth_apps.py): a confidential `Dev Schedule Bot` and a public `Dev Desktop Client`, both owned by `staff_user`, and per community a read-only grant whose bearer is `wizzrobe_app_devseed_<slug>_local_only_do_not_use`.
