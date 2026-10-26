# Authentication & Authorization Reference

_Discord OAuth login, session storage, route protection, the role catalogue, and the `AuthService` authorization API. Part of the [documentation index](../README.md)._

Sources: [`pages/auth.py`](../../pages/auth.py) (all OAuth routes and their mock variants), [`middleware/auth.py`](../../middleware/auth.py) (`protected_page` / `public_page` / `protected_tab_page` + `AuthMiddleware`), [`application/services/auth_service.py`](../../application/services/auth_service.py), [`application/services/user_service.py`](../../application/services/user_service.py) (login-time `User` provisioning), [`application/services/oauth_handoff_service.py`](../../application/services/oauth_handoff_service.py), [`application/services/discord/discord_role_mapping_service.py`](../../application/services/discord/discord_role_mapping_service.py), [`application/utils/environment.py`](../../application/utils/environment.py), [`application/utils/mocks/mock_discord.py`](../../application/utils/mocks/mock_discord.py), [`frontend.py`](../../frontend.py), [`models/user.py`](../../models/user.py) (`UserRole`), [`models/enums.py`](../../models/enums.py) (`Role`).

**Tenant scoping.** Authorization is evaluated **within the current tenant**: roles are per-tenant (`UserRole.tenant`) and the one global `SUPER_ADMIN` (`tenant=NULL`) bypasses the per-tenant gate. Identity is global. Where the OAuth callback runs depends on the login topology (see [Custom-domain login](#custom-domain-login-design-a-vs-design-b)). Tenant-aware session namespacing and redirect mechanics: [features/multitenancy.md § Auth, sessions, and OAuth](../features/multitenancy.md#auth-sessions-and-oauth).

## Overview

Wizzrobe authenticates users with Discord OAuth (`identify` scope only) and keeps their identity in NiceGUI's signed per-browser session store (`app.storage.user`). [`pages/auth.py`](../../pages/auth.py) registers the OAuth routes; [`middleware/auth.py`](../../middleware/auth.py) provides the page decorators and `AuthMiddleware`, which redirects unauthenticated requests for protected paths to `/login`. Authorization is a separate, stateless layer: [`AuthService`](../../application/services/auth_service.py) answers role and capability questions against the database on every check — nothing about roles is cached in the session. Under `MOCK_DISCORD=true` the OAuth routes are replaced by a local user-picker and no Discord credentials are needed.

Two non-session credentials exist alongside this, both stored in `ApiToken` and resolved through `ApiTokenService.resolve_actor`: **personal access tokens** for the REST API (tenant-bound) and **OAuth 2.1 tokens** for the MCP server (platform-wide). Each surface refuses the other's kind, so a credential minted for one can never be replayed against the other. Wizzrobe is itself the authorization server for the MCP flow — Discord still authenticates the human, at the consent screen — see [features/mcp-server.md](../features/mcp-server.md).

## Roles

Defined in [`models/enums.py`](../../models/enums.py) as `Role(str, Enum)` — twelve members. The first eight are per-tenant community roles; the next three are per-tenant online-tournament admin roles; `super_admin` is the one global platform role.

Inside a community, the seven on-site roles (`proctor`, `stream_manager`, `triforce_submitter`, `volunteer_coordinator`, `equipment_manager`, `volunteer`, `check_in_desk`; `Role.per_event()`, `role.is_per_event`) are held **in one event**, and `staff`, `preset_manager`, `sync_admin` and `qualifier_admin` (`Role.community_wide()`) cover every event. See [Per-event roles](../features/events.md#per-event-roles-phase-4).

| Role | Who has it | Grants |
|---|---|---|
| `staff` | Tournament organizers | Full admin dashboard; all CRUD; grant/revoke roles |
| `proctor` | Race monitors | Race/schedule workflow on `/volunteer`; seat/start/finish, record the result, roll seeds, assign stations (`can_run_match`) — **not** confirming a result, which is the admin's step (`can_confirm_match`). No Admin access |
| `stream_manager` | Broadcast techs (shown as **Broadcast Tech**; the stored value kept its old name so grants and Discord/volunteer mappings carry over) | Admin → Match Schedule and Stages only; stage assignment and the stream-candidate flag on any match; CRUD on stages; runs a match **on a stage** like a proctor (`can_run_match`), never confirms. Not in `can_view_admin`, so no admin REST/MCP scope, reports or staff help |
| `triforce_submitter` | Paid submitters | Submit Triforce texts on active tournaments whose generator supports them (no Admin access) |
| `volunteer_coordinator` | Volunteer leads | Admin → Volunteers, Volunteer Shifts and the Volunteer Coverage report; manage volunteer positions, shifts, assignments; the published schedule on `/volunteer`. Not in `can_view_admin`: over REST/MCP it reads only the volunteer admin data (`can_view_volunteer_admin`) |
| `equipment_manager` | Equipment leads | Admin → Equipment only; CRUD on lending assets; check equipment in/out; view private notes/owner. Not in `can_view_admin`, so no reports, crew lists or staff help |
| `volunteer` | General volunteers | Volunteer workflows on `/volunteer` (no Admin access) |
| `check_in_desk` | Check-in volunteers | Work the event check-in desk at Volunteer → Check-in Desk: check people in, link registrants to accounts, sync (`can_run_check_in_desk`). Walk-ups and event setup stay staff-only. No Admin access |
| `preset_manager` | Seed-preset authors | Author/edit the tenant's seed-rolling presets (`can_manage_presets`) |
| `sync_admin` | Sync/integration admins | Manage upstream sync config: SpeedGaming links, Discord events, racetime bot/room config (`can_manage_sync`) |
| `qualifier_admin` | Qualifier admins | Administer async qualifiers — author pools/permalinks, work the reviewer queue (`can_admin_qualifier`) |
| `super_admin` | Platform operators | `/platform` tenant management; bypasses the per-tenant role gate (`is_super_admin`) and is staff-equivalent inside **every** tenant |

Roles live in the `UserRole` junction table (`user`, `tenant`, `event`, `role`, `granted_by`, `source`, `created_at`; unique on `(user, role, tenant, event)`, plus the partial index `uid_userrole_community` on `(user, role, tenant)` where `event` is NULL). `event` is set for a per-event role and NULL for a community-wide one — there is **no** `permission` field on `User`. `source` is `manual` or `discord`: guild-role sync only ever revokes the `discord`-sourced rows it created, so manual grants survive ([features/discord.md § Guild-role → app-role sync](../features/discord.md#guild-role--app-role-sync)). `super_admin` is the only role whose row may carry `tenant=NULL`; it is never granted per-tenant and is checked via `is_super_admin`, not the tenant-scoped path.

**"Never granted per-tenant" is enforced, not just documented.** `Role.tenant_grantable()` is the list every per-tenant role surface reads — the Users dialog's picker, the Discord role-mapping picker, and the gates in `UserService.grant_role`/`revoke_role` and `DiscordRoleMappingService.add_mapping` — and it excludes `SUPER_ADMIN`. It has to be a gate rather than a convention because `can_grant_roles` is `is_staff`, which is evaluated inside the *actor's own* community: a check that stops there is asking a tenant-local question about a platform-wide answer, and STAFF anywhere could otherwise mint an account with authority over every community. `sync_user_roles` filters the same list over **stored** mapping rows, so a row that predates the guard cannot pay out on someone's next login. The only grant path is `TenantService.grant_super_admin` on `/platform`, which is why it is also the only audit action for the role. Tests: `tests/tenancy/test_platform_role_escalation.py`.

**Super-admin authority inside a tenant** is implemented in exactly one place — `AuthService.is_staff` returns True for the global role — because STAFF is the override term in essentially every `can_*`/`ensure_*` helper, so widening it there carries the platform role through the whole policy surface instead of scattering `is_super_admin` across dozens of call sites. Two deliberate limits: `get_roles`/`has_role` stay **literal** (they answer "which grants does this user hold here", which is what role-management UI displays — presentation code that needs the *gate* must call `is_staff`), and **feature flags are not bypassed** (a flag that is off hides its subsystem from the super-admin too). Tests: `tests/services/test_super_admin_authority.py`.

## OAuth flow mechanics

### Routes

`pages/auth.py:create()` registers the Discord bot-authorization callback unconditionally, then either the real OAuth routes or — under `MOCK_DISCORD` — the mock replacements (see [Mock authentication](#mock-authentication-dev)). It is called from [`frontend.py`](../../frontend.py) `init()`.

| Route | Mode | Behavior |
|---|---|---|
| `/oauth/discord/connect/callback` | always | Bot-authorization callback for linking a tenant to a Discord guild. Runs on the bare platform host, so the target tenant, CSRF `state`, and return path travel in the session (`discord_connect_*`); writes are wrapped in `tenant_scope`. Delegates to `DiscordLinkService.complete_link` (or `link_guild` in mock mode). |
| `/login` | real | If already authenticated, redirects to the safe `?next=` (else the tenant home). Otherwise pins the return path (see [Coming back to the page](#coming-back-to-the-page)), generates a CSRF `state`, and 302s to Discord's authorize URL. Under Design B on a custom domain it instead stashes a `handoff_bind` secret and redirects to the platform host's `/oauth/start`. |
| `/logout` | real + mock | `app.storage.user.clear()` — wipes the entire session — then redirects to the tenant home. |
| `/oauth/callback` | real | Validates `state`, exchanges the `code`, fetches the Discord user, provisions the `User` row, writes the session, syncs Discord roles, then navigates to `referrer_path`. |
| `/oauth/start` | real | Design B entry point on the platform host: allow-lists the target host against active tenant domains, stashes `handoff_*` keys, and runs the normal Discord flow to the single platform callback. |
| `/session/claim` | real | Design B claim on the custom domain: validates the single-use handoff token, checks the browser binding, re-checks `is_active`, writes the session. |

### CSRF state

`/login` binds each attempt to a one-time token: `secrets.token_urlsafe(32)` stored at `app.storage.user['oauth_state']` and appended to the authorize URL as `state=...`. On the callback the stored value is **popped** (single use) before any validation and compared to the returned `state`. A missing or mismatched state notifies "Login session expired or invalid" and navigates back to `/login`.

### Callback processing

`/oauth/callback` is an async NiceGUI page. It waits for `client.connected()`, then reads the full URL via `ui.run_javascript('window.location.href')` and parses the query string from it (rather than from the server-side request). It handles, in order: an `error` parameter (user cancelled/denied), the state check, and a missing `code` — each failure path notifies and navigates to `/login`. Any unexpected exception is logged and produces a negative notify plus a `/login` redirect.

The exchange `redirect_uri` is **rebuilt per request** from the callback's own browser-URL host (`_redirect_uri_for_tenant(await _callback_tenant(url))`), because Discord requires the exchange to match the authorize leg byte for byte. Both zenora calls — `oauth.get_access_token(code, redirect_uri)` on the module-level `APIClient`, then a short-lived `APIClient(access_token, bearer=True)` for `users.get_current_user()` — run inside `asyncio.to_thread`: zenora is synchronous (requests-based), and two inline Discord round-trips would block the single shared event loop for every connected user.

An account with `is_active=False` is rejected here: the session is cleared, a negative notify explains the account is inactive, and the browser goes back to `/login`. `/session/claim` re-checks it, since a deactivation can land inside the handoff token's TTL.

### Custom-domain login (Design A vs Design B)

`HOST_OAUTH_MODE` ([`environment.py`](../../application/utils/environment.py) `host_oauth_handoff_enabled()`) selects between two topologies:

| | Design A (default) | Design B (`HOST_OAUTH_MODE=handoff`) |
|---|---|---|
| Where OAuth runs | On whichever host the user is on — the custom domain in host mode, the platform host in path mode | Always on the platform host |
| Discord redirect URIs | One registered URI **per custom domain** | One, regardless of domain count |
| Tenant at the callback | Recovered from the callback's browser-URL host (`_callback_tenant`), so the tenant is in scope | Tenant-less; the target host travels in the platform session |
| Session handover | None — the cookie is already on the right host | Single-use, host-bound token → `/session/claim` on the target domain |

Design B's session keys and guards: `/login` on the custom domain mints a `handoff_bind` secret, keeps it in **that domain's** session, and publishes only its sha256 through the platform hop. `/oauth/start` records `handoff_target_host`, `handoff_next`, `handoff_bind_commit` in the platform session. The platform callback does **not** authenticate its own session — it mints a token via `oauth_handoff_service.mint(...)` and redirects to `/session/claim?token=…`, which compares the stashed secret's hash against the token's commitment with `hmac.compare_digest`. That comparison is the login-CSRF guard: a token delivered to a different browser lacks the secret and is rejected. A failed mint falls through to a normal platform-host login rather than stranding the user.

### Session keys (`app.storage.user`)

| Key | Written by | Purpose |
|---|---|---|
| `authenticated` | every sign-in, via `pages/_session.start_session` | `bool` flag that `AuthMiddleware` checks |
| `user_id` | every sign-in, via `start_session` | **The identity key**, resolved by `get_user_from_session(app.storage.user)`. A session from before this key existed resolves by `discord_id` and gains it |
| `discord_id` | Discord callback / claim / mock login | The Discord identity; absent on a password session. Still read to recognise "my" rows on the player and crew boards |
| `auth_method` / `password_stamp` | `/password-login` | Mark a password session and record the account's `password_changed_at`; a password change or removal moves it, which ends the session |
| `username` | callback / claim / mock login | Display convenience |
| `avatar` | callback / claim (`avatar_url`; `None` in mock mode) | Display convenience |
| `oauth_state` | `/login`, `/oauth/start` | One-time CSRF token; popped by the callback |
| `referrer_path` | `AuthMiddleware`, `/login` | Original (tenant-qualified) destination, **query string included**; popped after the post-login redirect |
| `handoff_bind` | `/login` on a custom domain (Design B) | Browser-binding secret; popped and verified by `/session/claim` |
| `handoff_target_host` / `handoff_next` / `handoff_bind_commit` | `/oauth/start` (Design B) | Target domain, return path, and the published sha256; popped by the platform callback |
| `discord_connect_state` / `discord_connect_tenant_id` / `discord_connect_return` | [`pages/admin_tabs/admin_discord_roles.py`](../../pages/admin_tabs/admin_discord_roles.py), before the bot-auth redirect | CSRF token, target tenant, and return path for `/oauth/discord/connect/callback` |

The Discord `access_token` is **never persisted** — not on the `User` row, not in the session — so there is nothing token-shaped to revoke at logout. The post-login target is `referrer_path` (default `/`), falling back to `/` when it names one of the auth routes.

### Coming back to the page

A Discord DM's button is usually opened on a phone with no session, and the whole point of the button is the dialog or row it names (`/home/my-schedule?match=1`, `/admin/match-schedule?match_id=1`). Two paths carry that target through login, and both keep the query string:

- **A protected page** bounces to `/login`, and `AuthMiddleware` stores `referrer_for(root_path, path, query)` as `referrer_path`.
- **A page anyone can open** — the join door (home applies the membership gate itself, so a signed-out *member* lands there too), the bracket views, help, the event handbook, the error pages — has a sign-in button rather than a bounce. Both the door's **Sign in** and the header's **Login with Discord** navigate to `login_path(current_local_url())`: `/login?next=<tenant-local path and query>`. The door's button used to be the relative `'login'`, which from `/home/<section>` resolved to `/home/login` — home again, so the door looped.

`/login` (real and mock) picks the target with `return_path_for_login(root_path, next, referrer_path)` ([`tenant_urls.py`](../../application/utils/tenant_urls.py)): a safe `next` wins, qualified with this tenant's `root_path`; otherwise the stored referrer goes through `sanitize_return_path` (this tenant only); otherwise the tenant home. **`next` is attacker-controlled**, so `safe_local_path` accepts only a plain same-origin path: it refuses anything with a scheme, a protocol-relative `//host`, the backslash form browsers normalise (`/\host`), control or whitespace characters, dot segments (including the `%2e` spelling, since `/t/a/../b` walks into another community), auth routes, and anything over 2 KB. `sanitize_return_path` applies the same shape check to the stored referrer, which matters in host mode where there is no prefix to anchor on, and the cross-host handoffs' `safe_next` (Design B's `/oauth/start` and `/session/claim`, and the secondary-provider link handoff) is the same gate with a `/` fallback — there is one definition of a safe return path. Tests: `tests/tenancy/test_login_return_path.py`.

**How the target is delivered matters.** `referrer_path` is stored *tenant-qualified* (`AuthMiddleware` writes `f'{root_path}{path}?{query}'`), and the two delivery mechanisms treat that differently:

- An HTTP `RedirectResponse` is resolved by the browser against the origin, so it takes the fully qualified path — `tenant_home(root_path)`, `referrer_path` as stored.
- `ui.navigate.to` is **client-side**, and `nicegui.js` prepends the client's `options.prefix` (`X-Forwarded-Prefix` + `root_path`) to any absolute path. A navigate issued from a page served under `/t/<slug>` must therefore be given the **tenant-local** path via `strip_root_path(root_path, path)`, or it lands on `/t/<slug>/t/<slug>/…`. The mock-login picker (`_login_as`) does this. The real `/oauth/callback` and `/session/claim` deliberately do not: both run where the prefix is empty — the callback on the bare platform host, the claim on the tenant's own domain — so the qualified path is the correct one there.

`User` row writes go through `UserService.provision_from_discord_login(discord_id, username)` (the presentation layer performs no direct ORM write):

- **New user** — created with `discord_id` + `username`, and a `user.provisioned` audit entry self-attributed to the new user.
- **Existing user** — `username` is overwritten and saved; all other fields (including `display_name`) untouched.
- **Inactive user** — returned without a username update so the callback can reject the login.

Roles are not written by the callback itself; `DiscordRoleMappingService().sync_user_roles(user)` may map guild roles onto `UserRole` rows (self-defensive, never blocks login), and every other role comes from a `UserRole` row or a tournament membership.

## Account linking (Challonge / Twitch / racetime.gg)

Separate from login: a signed-in user links a **provider identity** so we can find their bracket matches, attribute their race results, and check auto-open eligibility. The verified `(id, name)` is recorded on the global `User` row (`<provider>_user_id`, `_username`, `_linked_at`); no provider access token is ever retained. Each id column is `unique=True`, so a provider account resolves to exactly one user.

Surfaced on Profile → **Connected accounts** ([`pages/home_tabs/_link_section.py`](../../pages/home_tabs/_link_section.py), one `LinkSectionConfig` per provider). A provider whose integration is not configured is **hidden rather than offered** — with none configured the whole card is omitted. Challonge additionally requires `FeatureFlag.CHALLONGE`.

| Route | Purpose |
|---|---|
| `/<provider>/link` | Initiation. Tries the cross-host handoff, then the mock short-circuit, then the Design A detour, then real OAuth |
| `/<provider>/oauth/callback` | Exchange + record. Also serves the platform-host leg of a handoff |
| `/oauth/link/start` | Platform-host entry point for a custom domain's handoff. Refuses unless `HOST_OAUTH_MODE=handoff` |
| `/oauth/link/claim` | Custom-domain claim: validates the token and browser binding, records the link. Refuses in path mode, where no token can have been minted |

The engine is shared ([`pages/_oauth_link.py`](../../pages/_oauth_link.py)); `challonge_oauth.py` registers its own pages because **one registered redirect URI serves two flows** — the STAFF service-account connect and the player identity link. Which one a callback completes is decided by matching the returned CSRF `state` against the pending ones by name; a callback that matches neither completes neither, and returns to the profile rather than the admin area.

The **cross-host handoff** is the linking counterpart of Design B above and turns on with the same `HOST_OAUTH_MODE=handoff`: the provider OAuth runs on the platform host, and only the public identity crosses back to the custom domain in a single-use, host-bound, browser-bound token (`oauth_link_bind` / `oauth_link_bind_commit`, verified fail-closed). With handoff **off** on a custom domain the row shows "Main site only" as a link to the platform-host equivalent of the profile page, because the callback would land on a host that cannot see this domain's cookie.

**Failure messages survive the redirect.** Every link outcome is queued with `stash_notice` ([`theme/notice.py`](../../theme/notice.py)) and shown on the page the user lands on — `ui.notify` followed by `ui.navigate.to` loses the toast with the document. The platform host cannot write into the target domain's session, so a handoff it gives up on hands back through `/oauth/link/claim?r=<reason>` and the claim route turns the reason into the message.

Driving all of this without provider credentials: [development.md](../development.md#account-linking-without-provider-credentials).

### OAuth URL derivation

The Discord credentials (`DISCORD_TOKEN`, `DISCORD_CLIENT_SECRET`, `DISCORD_CLIENT_ID`, plus `STORAGE_SECRET`) are read **once at import** of `pages/auth.py`. The URL-shaping variables are read **lazily per request**, so a per-deploy change applies without a reimport. See [`.env.example`](../../.env.example) and [deployment.md](../deployment.md) for the full variable table.

| Variable | Required | Default / derivation |
|---|---|---|
| `DISCORD_CLIENT_ID` | yes (real OAuth) | Embedded in the authorize URL |
| `DISCORD_CLIENT_SECRET` | yes (real OAuth) | zenora client secret for the code exchange |
| `DISCORD_TOKEN` | yes (real OAuth) | Bot token for the module-level zenora client |
| `PLATFORM_HOST` | no | Netloc of `BASE_URL`. The host serving the tenant-agnostic surface and every path-mode tenant |
| `REDIRECT_URL` | no | `{scheme}://{PLATFORM_HOST}/oauth/callback`. An explicit override only; on a custom domain the URI is built from the tenant's stored `domain`, never from `BASE_URL` or a reflected `Host` header |
| `OAUTH_URL` | no | `https://discord.com/api/oauth2/authorize?client_id=…&redirect_uri=…&response_type=code&scope=identify&prompt=none`, built per request; an explicit value gets `state` appended (and no `prompt`, so add it yourself if you want it). `prompt=none` skips Discord's Authorize screen for someone who has already approved the app |
| `HOST_OAUTH_MODE` | no | Unset = Design A; `handoff` = Design B |

## Route protection

`AuthMiddleware` enforces **login** for registered protected paths; the page decorators enforce **tenant, feature flag, and roles** at render time. All three decorators share one implementation, `_tenant_page`.

### Every tenant page

On each render, `_tenant_page` runs in this order — regardless of which decorator registered the route:

1. **Page-view telemetry** — recorded before any auth short-circuit, in a background task with the tenant rebound ([features/telemetry.md](../features/telemetry.md)). On a public page the row may carry a `NULL` `discord_id`, attributed to the browser session alone.
2. **Tenant resolution** — no tenant in scope (a bare `/help` on the platform host rather than `/t/<slug>/help`) renders a 404 titled "Pick a community" whose buttons open the same path inside each active community — someone who lost the prefix wants the page, not a dead end.
3. **Stash for websockets** — `stash_client_tenant_id(tid)` and `stash_client_host_mode(is_host_mode())`, so UI event handlers running outside any request can resolve both.
4. **Display clock** — binds the viewer's display timezone (contextvar + client stash) from `TimezoneService.pick(...)` before anything renders, first without the user and again once a signed-in user is loaded in step 6 ([timezone-handling.md](../timezone-handling.md)). A page that bypasses `_tenant_page` (the tenant home) calls the public `bind_display_timezone(tenant_id, user)` itself.
5. **Feature gate** — when `feature=` is set and the flag is not live for this tenant, renders the unknown-route **404**, word for word and with the viewer shown (`theme.error_page.render_not_found`), *before* the role gate: a subsystem the tenant has not enabled is invisible to everyone, staff and super-admins included.
6. **Membership gate** (only on an auth-requiring route, so a `public_page` is unaffected) — a viewer who is not a `TenantMembership` of this tenant gets the **join door** ([`theme/join_page.py`](../../theme/join_page.py)), *not* a 403: forbidden-by-role is a dead end, not-a-member is a state with a remedy. Rendered in place at the requested URL, so a deep link survives approval. `SUPER_ADMIN` bypasses it, as it does the role gate. Deliberately not gated on `Tenant.is_active`. The check is `middleware.auth.enforce_membership`, shared with the tenant home — which is registered with a bare `ui.page` (the same function also serves the tenant-less community picker) and so applies the gate itself.
7. **Role gate** (only when `roles=` or `allow_tournament_membership=` was given) — a global `SUPER_ADMIN` passes unconditionally; otherwise the user must hold at least one listed role **in the current tenant** (`AuthService.get_roles` intersection), and `allow_tournament_membership=True` additionally admits anyone `AuthService.can_view_admin` accepts. Denial renders the themed 403 page ([`theme/error_page.py`](../../theme/error_page.py)) in the community's chrome — a render with HTTP status 403, not a redirect. See [Error pages](frontend.md#error-pages-middlewareerror_handlerspy).

### `protected_page`

```python
def protected_page(
    path: str,
    *,
    roles: Optional[Iterable[Role]] = None,
    allow_tournament_membership: bool = False,
    feature: Optional[FeatureFlag] = None,
    telemetry_path: Optional[str] = None,
    **page_kwargs,
)
```

Adds `path` to the module-level `protected_routes` registry (consumed by `AuthMiddleware`), then registers the function with `ui.page(path, **page_kwargs)`. With no `roles` and no `allow_tournament_membership`, login is enforced only by the middleware and there is no render-time authorization — such a page must handle its own gating in the body, and must tolerate a valid session whose `User` row no longer resolves. `telemetry_path` lets sibling routes rendering the same page report under one stable path.

### `public_page`

```python
def public_page(
    path: str,
    *,
    feature: Optional[FeatureFlag] = None,
    telemetry_path: Optional[str] = None,
    **page_kwargs,
)
```

Identical to `protected_page` minus the `protected_routes` registration, the **membership gate**, and the role gate, so `AuthMiddleware` lets an anonymous request through. There is deliberately no `roles` argument: a page that authorizes everyone cannot also authorize a role. The body must tolerate `user is None` throughout (`get_user_from_discord_id(None)` returns `None` and every `AuthService` predicate is `False`, so signed-in affordances hide themselves), and everything it renders is world-readable by design.

Signed-out readability is **not** publication: the app serves a blanket `robots.txt` (`frontend._register_root_routes`) and `BaseLayout.render_chrome` stamps a `noindex, nofollow` meta. Page routes carry **no rate limiting** — `api/rate_limit.py` is mounted on the REST router only.

### `protected_tab_page`

`protected_tab_page(base, **kwargs)` registers a tabbed hub under **both** `base` and `base/{section}` with a shared `telemetry_path=base`, so the section slug lives in the path (`/admin/match-schedule`) and both routes report as one page view. It calls `protected_page` twice rather than stacking decorators, which would not work: `protected_page` returns the `ui.page` object, not the function.

### Current usages

| Decorator | Route(s) | Gating |
|---|---|---|
| `protected_tab_page` | `/admin` ([`pages/admin.py`](../../pages/admin.py)) | Login-only; the page body does its own tab-by-tab authorization |
| `protected_tab_page` | `/volunteer` ([`pages/volunteer.py`](../../pages/volunteer.py)) | `roles=[VOLUNTEER, PROCTOR, STAFF, CHECK_IN_DESK, VOLUNTEER_COORDINATOR, STREAM_MANAGER]`, no page-level flag; the volunteer tabs follow `can_view_volunteer`, Proctor Station `can_open_proctor_station` (a Broadcast Tech's board lists only matches on a stage, `proctor_station_on_stage_only`), the desk tab `can_view_check_in_desk`, and a visitor with no tab gets the not-found page a flag would give |
| `protected_page` | `/equipment/{asset_id}` ([`pages/equipment.py`](../../pages/equipment.py)) | `feature=EQUIPMENT` |
| `protected_page` | `/equipment/qr-labels` ([`pages/equipment_labels.py`](../../pages/equipment_labels.py)) | `roles=[STAFF, EQUIPMENT_MANAGER]`, `feature=EQUIPMENT` |
| `protected_page` | `/checkin`, `/checkin/{event_id}` ([`pages/check_in.py`](../../pages/check_in.py)) | `roles=[STAFF, CHECK_IN_DESK]`, `feature=EVENT_CHECK_IN`; both redirect to the Volunteer hub's desk tab |
| `protected_page` | `/qualifiers`, `/qualifiers/{qualifier_id}` ([`pages/qualifiers.py`](../../pages/qualifiers.py)) | `feature=ASYNC_QUALIFIERS` |
| `public_page` | `/tournament/{tournament_id}/brackets`, `/brackets/{bracket_id}` ([`pages/brackets.py`](../../pages/brackets.py)) | `feature=BRACKETS`; spectator surfaces that must work signed out |
| `public_page` | `/event-info`, `/event-info/{slug}` ([`pages/event_info.py`](../../pages/event_info.py)) | `feature=EVENT_INFO` |
| `public_page` | `/help`, `/help/{slug}` ([`pages/help.py`](../../pages/help.py)) | None — in-app help articles |
| `public_page` | `/room/{token}/seeds` ([`pages/room_seeds.py`](../../pages/room_seeds.py)) | None; the URL token is the credential (`telemetry_path='/room/seeds'`, and the token value is redacted from telemetry) |
| `public_page` | `/facts`, plus the `/cat-facts`, `/hadestown-facts` and `/rollercoaster-facts` redirects ([`pages/facts.py`](../../pages/facts.py)) | None |

`pages/mcp_consent.py` is a bespoke `@ui.page` that registers itself in `protected_routes` directly, because an MCP grant is platform-wide and must not 404 on the tenant check ([features/mcp-server.md](../features/mcp-server.md)). Page registration order is described in [frontend.md](frontend.md).

### `AuthMiddleware`

Registered in [`frontend.py`](../../frontend.py) with `app.add_middleware(AuthMiddleware)` at module import. Its `dispatch` runs on every request:

1. If `app.storage.user['authenticated']` is truthy, attach the `discord_id`/`username` to Sentry (guarded, so `str(None)` never files a phantom user) and pass through.
2. Otherwise, if the path does not start with `/_nicegui` **and** `_matches_protected_route(path)`: save `referrer_for(root_path, path, request.url.query)` (the query kept) as `referrer_path` and return `RedirectResponse(f'{root_path}/login')`. Under path mode `TenantMiddleware` has already stripped `/t/<slug>` into `root_path`, so rebuilding both from it round-trips the login back to the right community.
3. All other unauthenticated traffic (home page, `/api/*`, static files) passes through.

`_matches_protected_route` matches plain paths by **exact string equality** (no prefix matching — `/admin/foo` does not match `/admin`), and compiles entries containing `{param}` placeholders to anchored regexes with each placeholder as `[^/]+`, so `/equipment/{asset_id}` matches `/equipment/3`.

## Session storage & security

`app.storage.user` is NiceGUI's per-browser session store, signed with `STORAGE_SECRET` — [`frontend.py`](../../frontend.py) passes `storage_secret=(os.environ.get('STORAGE_SECRET') or '').strip()` to `ui.run_with()`. The entire authorization model trusts the `discord_id` in this signed store, which is why startup refuses to proceed without the secret.

`validate_security_config()` ([`environment.py`](../../application/utils/environment.py)) is called first thing in `frontend.init()` and raises `RuntimeError` — aborting startup before any request is served — when:

| Check | Environment |
|---|---|
| `STORAGE_SECRET` non-empty (after strip) | always |
| `STORAGE_SECRET` length **≥ 32** (after strip) | production only |
| `DB_USERNAME` non-empty | production only |
| `DB_PASSWORD` non-empty | production only |

"Production" means `ENVIRONMENT=production` (`get_environment()` lowercases and strips; default `development`). The error message and [`.env.example`](../../.env.example) direct operators to a strong random value (e.g. `secrets.token_urlsafe(32)`).

The `MOCK_DISCORD` production refusal is **not** part of `validate_security_config()` — it lives in `is_mock_discord()` ([`mock_discord.py`](../../application/utils/mocks/mock_discord.py)), which raises `RuntimeError` whenever `MOCK_DISCORD` is truthy (`env_flag`: `1`/`true`/`yes`/`on`, stripped and lowercased) while `ENVIRONMENT=production`. Because `middleware/auth.py` calls it at import time, this also aborts startup. Rationale: mock mode turns `/login` into an unauthenticated impersonate-anyone page.

`/logout` clears the whole session; there is no server-side session table to invalidate.

## Authorization API (`AuthService`)

[`AuthService`](../../application/services/auth_service.py) is a stateless collection of async static methods. Every check accepts `Optional[User]` and treats `None` as "no access" — callers never need a guard. The intended pattern: resolve the user **once** at page entry with `get_user_from_discord_id(app.storage.user.get('discord_id'))`, then pass the model into the helpers. All checks query the database live; nothing is cached.

### Identity and role reads

| Method | True when / behavior |
|---|---|
| `is_system(user)` | The reserved automation actor (`User.is_system`) — a field check, not a role query. Gated helpers short-circuit on it so workers and bot handlers never hit a `PermissionError` |
| `is_super_admin(user)` | A `UserRole` row with `role=SUPER_ADMIN, tenant=NULL, event=NULL` exists |
| `get_roles(user, event=None)` | `set[Role]` held **in the current tenant** (empty for `None`, and empty on a tenant-less surface such as `/platform`); excludes `SUPER_ADMIN`. Community-wide grants always count; per-event grants count when held in `event`: an event id, a `RoleScope` (`LIVE`, `OPEN` = live + draft, `EVERY`), or None for the bound event (`LIVE` when none is bound) |
| `has_role(user, role, event=None)` | Holds that role **in the current tenant**, with `event` read as for `get_roles`; `False` outright when no tenant is in context. Row checks pass the row's `event_id` |
| `holds_any_role(user)` / `event_ids_with_grants(user)` | Any grant in a live or draft event (the membership rule) / the events the user holds per-event grants in |
| `is_staff(user)` | Holds `STAFF` in the current tenant **or** is the global `SUPER_ADMIN`. The single place the platform role becomes in-tenant authority — see [Roles](#roles). Use `has_role`/`get_roles` where the literal grant matters (role-management UI) |
| `is_proctor` / `is_stream_manager` / `is_triforce_submitter` / `is_volunteer_coordinator` / `is_equipment_manager` / `is_volunteer` | `has_role` shorthands for the matching `Role` member; all but `is_equipment_manager` take `event`. Equipment is a community page, so `is_equipment_manager` counts a grant in any live event (`RoleScope.LIVE`) |
| `is_tournament_admin(user, tournament_id)` | In that tournament's `admins` M2M, filtered to the current tenant (PKs are global) |
| `is_crew_coordinator_of(user, tournament_id)` | In that tournament's `crew_coordinators` M2M, tenant-filtered the same way |

### Capability gates

| Method | True when |
|---|---|
| `can_view_admin(user)` | Staff (global `SUPER_ADMIN` included), or TA/CC of at least one tournament **in this tenant** (the reverse relations hang off the global `User`, so they are tenant-filtered). The Reports tab's audience; gates `require_admin`, MCP `Gate.ADMIN` and `audience: staff` help. Every other role (broadcast tech, volunteer coordinator, equipment manager, the online-play roles, proctor, volunteer) is held to its own tab gates |
| `can_view_volunteer_admin(user)` | `can_view_admin` or `can_manage_volunteers` (staff or `VOLUNTEER_COORDINATOR`): the admin-side volunteer reads. Gates `require_volunteer_admin` and MCP `Gate.VOLUNTEER_ADMIN` |
| `can_open_admin(user)` | The nav's Admin link: `/admin` has at least one tab for this viewer. Mirrors `build_admin_tabs` flags included: super-admin, `STAFF`/`STREAM_MANAGER`, TA/CC, `PRESET_MANAGER`, `SYNC_ADMIN`; `VOLUNTEER_COORDINATOR` with `VOLUNTEERS`, `EQUIPMENT_MANAGER` with `EQUIPMENT`, `QUALIFIER_ADMIN` or a per-qualifier admin with `ASYNC_QUALIFIERS`. Deliberately separate from `can_view_admin`, which also gates the admin API, MCP admin scope, reports and staff help |
| `can_open_proctor_station(user)` | A `PROCTOR` always (`can_run_match` has no flag, so their one screen for it doesn't either); staff only where `can_view_volunteer` holds |
| `can_view_volunteer(user)` | The `VOLUNTEERS` flag is live **and** (super-admin or one of `VOLUNTEER`/`PROCTOR`/`STAFF`/`VOLUNTEER_COORDINATOR`). The nav's source of truth for the volunteer tabs, so the header link and `@protected_tab_page('/volunteer')` cannot drift into offering a link that 404s or 403s. The hub also opens for the desk (`can_view_check_in_desk`) |
| `can_edit_tournament(user, tournament)` | Staff, or TA of that tournament |
| `can_manage_payouts(user, tournament)` | Same people as `can_edit_tournament` — read **and** edit one tournament's prize split (an unannounced split is admin data, so reads are gated too) |
| `can_crud_match(user, match)` | Staff, or TA of the match's tournament |
| `can_run_match(user, match)` | Staff, **proctor**, a `STREAM_MANAGER` when the match has a stage, or TA of the match's tournament — running a match on the floor: seat/start/finish, recording the result, seed rolls, station assignment. Deliberately **not** confirming |
| `can_confirm_match(user, match)` | Staff, or TA of the match's tournament. **Excludes `PROCTOR` by design**: confirming advances the native bracket and pushes the result to Challonge, which is the admin's verification step — a proctor's workflow ends at recording the winner. `MatchScheduleService._transition` defaults to `can_run_match` and only `confirm_match` passes `authorize=can_confirm_match` |
| `can_approve_crew(user, match)` | Staff, TA, or CC of the match's tournament |
| `can_view_schedule_board(user)` | Staff, `STREAM_MANAGER`, or TA/CC of any tournament here — the same predicates `pages/admin.py` appends the Schedule tab on, so a report's drill-out link is never offered to someone the board would refuse. What they may *do* once there is a separate question, answered per capability by `MatchBoardAccess` ([frontend](frontend.md#match-board-capabilities-themetablesmatch_accesspy)) |
| `tournament_scope(user)` | Not a gate — returns `(admin_ids, coordinated_ids)` for this tenant. The *ids*, not just whether any exist, because a non-staff operator's board is scoped to them: a crew coordinator of one tournament has no business reading the whole community's schedule. Staff and the globally-scoped roles have no id set |
| `can_submit_triforce_text(user, tournament)` | The tournament is active, its generator is in `SeedGenerationService.TRIFORCE_TEXT_RANDOMIZERS`, and the user is staff or `TRIFORCE_SUBMITTER` |
| `can_manage_stages(user)` | Staff or stream manager — CRUD on `Stage` records themselves |
| `can_assign_match_stream(user, match)` | `can_manage_stages`, or TA of the match's tournament — sets `stage` / `is_stream_candidate` |
| `can_manage_volunteers(user)` | Staff or volunteer coordinator — positions, shifts, assignments (admin side) |
| `can_manage_equipment(user)` | Staff or equipment manager — CRUD on lending assets, private notes/owner |
| `can_checkout_equipment(user)` | Any signed-in, active member that is not the system account — **deliberately role-less**, because a loaner's QR code is scanned by whoever holds it. Grants self-checkout only; naming another borrower is `can_manage_equipment` |
| `can_checkin_equipment(user)` | `can_manage_equipment` |
| `can_manage_check_in(user)` | Staff — check-in event setup, walk-ups, removing a walk-up |
| `can_run_check_in_desk(user)` | Staff, `CHECK_IN_DESK`, or the system user — check in, undo, link/unlink, sync |
| `can_view_check_in_desk(user)` | `EVENT_CHECK_IN` live **and** `can_run_check_in_desk`; the nav link's source of truth |
| `can_manage_presets(user)` | The shared `_system_admin_staff_or` cascade with `PRESET_MANAGER` |
| `can_manage_sync(user)` | The same cascade with `SYNC_ADMIN` — SpeedGaming links, Discord events, racetime bot/room config |
| `can_admin_qualifier(user, qualifier=None)` | The same cascade with `QUALIFIER_ADMIN`, **or** — when a qualifier is passed — membership in its `admins` M2M |
| `can_grant_roles(user)` | Staff only — grant/revoke `UserRole` rows and tournament `admins`/`crew_coordinators` |

`_system_admin_staff_or(user, role)` is the shared ladder behind the three online-tournament gates: system actor → super-admin → staff → the named per-tenant role.

### Raising variants

| Method | Raises `PermissionError` unless |
|---|---|
| `ensure(allowed, message='Permission denied')` | `allowed` is truthy — the generic service-layer guard clause |
| `ensure_super_admin(user)` | `is_super_admin` |
| `ensure_can_manage_presets(user)` | `can_manage_presets` |
| `ensure_can_manage_sync(user)` | `can_manage_sync` |
| `ensure_can_admin_qualifier(user, qualifier=None)` | `can_admin_qualifier` |

### `get_user_from_session`

```python
async def get_user_from_session(storage) -> Optional[User]
```

How presentation code learns who is signed in: pass `app.storage.user` (or a `dict` copy of it, for a background task). Returns `None` when signed out, when the account is gone or deactivated, or when a password session's password has since changed or been removed. `session_user_id(storage)` reads the id without a query.

### `get_user_from_discord_id`

```python
async def get_user_from_discord_id(discord_id: str | None) -> Optional[User]
```

Re-exported from [`application/services`](../../application/services/__init__.py). Resolves a Discord id (a bot interaction, the Discord `/login` link) and returns `None` when it is absent (not logged in), when the row has been deleted since login, **or** when the account has been deactivated (`is_active=False`) — so a deactivation takes effect on the user's next request, matching the REST API's rejection in [`api/dependencies.py`](../../api/dependencies.py).

## Password sign-in

For people without Discord, or who can't use it at the event. A platform super-admin gives an account a **sign-in name** and **password** on `/platform?tab=users` ([`pages/platform_users.py`](../../pages/platform_users.py)), or creates an account with no Discord at all (**New user**). There is no self-service: nobody sets, changes or resets their own password.

- **Sign-in page**: `/password-login` ([`pages/password_login.py`](../../pages/password_login.py)), unlisted, on the platform host and under `/t/<slug>`. On a custom domain it redirects to the community's `/t/<slug>/password-login` on the platform host: whoever runs a custom domain's DNS or TLS can read what is typed there, and a password, unlike a host-bound session, works everywhere. Every failure shows one message. Tries are limited per sign-in name and per browser ([`application/utils/login_throttle.py`](../../application/utils/login_throttle.py)), never per IP, because venue Wi-Fi shares one address; each try is counted *before* the hash runs (`begin_attempt`), so parallel tries can't slip past the limit, and a success hands its slot back. At most four hashes run at once, so a flood of tries queues rather than exhausting memory.
- **Super-admins**: a platform super-admin can't sign in by password (no second factor, and the role opens every community). `authenticate` refuses them, `set_password` refuses to give them one, and `get_user_from_session` ends a password session whose account has since been made a super-admin.
- **Storage**: `User.login_name` (unique, lower-cased), `User.password_hash` (scrypt, verified off the event loop), `User.password_changed_at` (the session stamp). The DB CHECK `ck_user_identity` lets an account have no `discord_id` only if it is a SpeedGaming placeholder or has a password.
- **Service**: `PasswordAuthService` ([`application/services/password_auth_service.py`](../../application/services/password_auth_service.py)) authenticates and, for super-admins only, sets, removes, creates and (de)activates. `user.password_set` / `user.password_removed` go in the audit log without the password, and every successful password sign-in writes `user.password_signed_in` (stamped with the community it happened in).
- **Scope**: a password session gets exactly the roles and memberships of the account. A password-only account does staff and desk work; the player and crew boards still recognise "my" rows by Discord id, so playing and crew signup need Discord. DMs to an account with no Discord return `DM_NO_DISCORD` without trying.

## Mock authentication (dev)

When `is_mock_discord()` returns true, `create()` calls `_create_mock()`, which registers replacements at the same paths:

| Route | Mock behavior |
|---|---|
| `/login` | User-picker page (no Discord redirect): a filterable table of all users — username, display name, discord_id, and a roles summary including `TA(n)`/`CC(n)` counts — each with a "Log in as" button, plus a "Create new user" card (username, optional display name, random pre-filled numeric discord_id, multi-select of all `Role` values) that creates the `User` and `UserRole` rows via `UserService.create_mock_login_user` and logs straight in. Already-authenticated visitors go to the tenant home. |
| `/logout` | Identical to the real route. |
| `/oauth/callback` | Stub: unconditionally redirects to the tenant home. |

`_login_as(user)` writes exactly the real callback's session keys (`username`, `avatar=None`, `authenticated=True`, `discord_id`) and honors `referrer_path` with the same auth-route exclusion, so `AuthMiddleware`, the page decorators, and every `AuthService` check behave identically. No access token is involved and the zenora client is never constructed. Users created through the picker are real database rows and persist across restarts. The mode is refused at startup in production (see [Session storage & security](#session-storage--security)). Enable/use workflow and the `DiscordService` stubbing that accompanies it: [features/discord.md § Mock mode](../features/discord.md#mock-mode).
