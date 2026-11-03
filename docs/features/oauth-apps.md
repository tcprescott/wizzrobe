# Third-party apps (OAuth 2 authorization server)

_Backend reference for the `/oauth` authorization server that lets people sign in to outside apps with their Wizzrobe account. Part of the [documentation index](../README.md). The design and work plan live outside the repo; this page describes what the code does._

**Status:** behind `FeatureFlag.THIRD_PARTY_APPS` (ships dark). Backend, per-route scopes and the screens are built; per-app rate limits and CORS are later work items.

## Screens

| Where | File | What |
|---|---|---|
| `/oauth/consent?request=<id>` | [`pages/oauth_consent.py`](../../pages/oauth_consent.py) | One card: app, maker, Unverified tag, redirect host, community picker, scopes grouped *Let it see* / *Let it act as you*. Reads start ticked; writes and `staff:*` (tagged Staff) never do. Tenant-less `@ui.page` in `protected_routes`, like the MCP consent page |
| Profile → Developer apps | [`pages/home_tabs/developer_apps_section.py`](../../pages/home_tabs/developer_apps_section.py) | Folded section beside API tokens. New app asks *What are you building?* (website = confidential with secret, device = public/PKCE), then name, homepage, redirect URLs one at a time, scopes. Secret shown once; ⋮ Edit / Show client ID / Rotate secret / Delete |
| Profile → API tokens | [`pages/home_tabs/api_tokens_section.py`](../../pages/home_tabs/api_tokens_section.py) | App grants listed with the tokens, tagged App, scope chips, bin disconnects |
| Admin → Settings → Connected apps | [`pages/admin_tabs/settings/connected_apps.py`](../../pages/admin_tabs/settings/connected_apps.py) | Staff. One row per app connected here with member count (never who); ⋮ Block here / Unblock; switch that turns the flag off for the community after a confirm |
| `/platform?tab=apps` | [`pages/platform_apps.py`](../../pages/platform_apps.py) | Super admin. Every app with owner, kind, people, communities; ⋮ Enable / Disable (reason) |

**Community block** (`OAuthAppBlock`, one row per community × app): blocking revokes every grant the app holds in that community, hides that community on the consent card, refuses the token endpoint (`invalid_grant`) and the API (401). Other communities are untouched. Audited `oauth_app.blocked` / `oauth_app.unblocked`, no event (the revoked grants already stop the app).

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

## Limits and browser access

| Limit | Where | Value |
|---|---|---|
| Calls per app, across all its users | `enforce_app_limit` in `api/rate_limit.py`, run from `resolve_token` once the grant is known | `API_APP_RATE_LIMIT_PER_MIN`, default 600. Sits above each grant's own token bucket so one popular or runaway app can't take the single worker |
| Apps per person | `MAX_APPS_PER_OWNER` | 10 |
| New apps per person per day | `APPS_CREATED_PER_DAY` (in-process window) | 5, so delete-and-recreate can't mint client ids without end. A refused request (bad redirect, no scope) doesn't spend one |
| Parked sign-in requests | `application/utils/parked_requests.py`, shared with the MCP server's authorize step | 15 min each, at most 5,000; past that the oldest go first |

**CORS** (`middleware/app_cors.py`) covers `/api/*`, `/oauth/token` and `/oauth/revoke`. An origin is allowed when a live public app (not disabled, owner active) registered a redirect URI on it: https anywhere, or http on loopback for local development (`OAuthAppService.browser_origins`, cached 60 s). Preflights from other origins get 400 and no CORS headers; their plain requests get no CORS headers, as before. Credentials are never allowed, since the API takes bearer tokens and never the session cookie. The issuer metadata is readable from any origin. `/oauth/authorize` and the consent page stay same-origin: they are navigations, not fetches.

## Rules worth knowing

- **Scopes only narrow.** A grant does what its scopes cover *and* what the person's roles in that community allow. A grant with no write scope is also stamped `read_only`, so write routes refuse it twice over. Each REST route's scope is listed in `api/scopes.py`; a call outside the grant gets `403` with an `insufficient_scope` challenge (see [REST API › App scopes](../reference/rest-api.md)).
- **Consent** (`consent_view` / `approve` / `deny`): only communities the person belongs to, active, with the flag on. `staff:*` are offered only to someone holding an admin-side role there (`can_view_admin`, or any role besides Volunteer). The grant carries exactly the ticked scopes, which must sit inside the offer.
- **One live grant per person × app × community.** Approving again replaces the old grant.
- **Rotation and reuse.** Access tokens last 1 h, refresh tokens 30 days sliding and rotate on every use. Presenting a rotated-away refresh token revokes the grant (`oauth_grant.refresh_reused`). A replayed authorization code revokes the grant it produced.
- **Re-checked at each exchange and each API call:** flag on, community active, account active, still a member, app not disabled and owner active. Flag off answers 404 at `/api` and `invalid_grant` at the token endpoint, without revoking anything.
- **Registration:** anyone signed in, 10 apps each, 5 new ones a day. Name ≤ 60, description ≤ 300, plain text with control and bidi characters stripped; homepage https only. Redirects follow the MCP rules (`is_allowed_redirect_uri`), max 10, and a confidential app may only use https or loopback http. Secret rotation keeps the old secret for 24 h. Deleting an app revokes every grant; a platform admin's disable stops them reversibly.
- **Revocation paths that already work:** the person (profile token list, since grants are `ApiToken` rows), the app (`/oauth/revoke`), member removal (`revoke_for_user_in_tenant`), app delete, platform disable.

## For app makers

The outside-facing guide is the help article `application/help/content/building-apps.md` (`/help/building-apps`, behind the flag like `/help/apps`). It links a stdlib-only example client served from `static/developers/wizzrobe_app_example.py`; `tests/oauth/test_developer_example.py` drives the script's own PKCE, authorize-URL and form builders through the test app, so a server change that breaks the published example fails CI.

## Audit and events

Audit: `oauth_app.created/updated/secret_rotated/deleted/disabled/enabled/blocked/unblocked`, `oauth_grant.created/revoked/refresh_reused`. Events: `oauth_grant.created` and `oauth_grant.revoked` (tenant-scoped, payload names the app and scope). App registration is global and stays audit-only.

## First-party app

Wizzrobe's own app (`web/`) is an `OAuthApp` with `first_party=True`: no consent screen, a grant with no community, and a community named per call in `X-Wizzrobe-Community`. Not offered on any page; `scripts/register_first_party_app.py` creates or updates it. Detail: [REST API § first-party](../reference/rest-api.md#wizzrobes-own-app-first-party).

## Dev seed

[`scripts/seed_oauth_apps.py`](../../scripts/seed_oauth_apps.py): a confidential `Dev Schedule Bot` and a public `Dev Desktop Client`, a `Dev Blocked App` blocked in every community (so Connected apps shows the Blocked state), all owned by `staff_user`, and per community a read-only grant whose bearer is `wizzrobe_app_devseed_<slug>_local_only_do_not_use`.
