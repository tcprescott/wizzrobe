# Multitenancy

Wizzrobe is **logically multitenant**: one process and one database serve many
independent tournament communities ("tenants"). Every request resolves to a
tenant, and the data layer scopes reads and stamps writes to that tenant so
communities never see each other's data. The per-task scoping obligations are in
CLAUDE.md; this doc is the mechanism behind them.

The unifying rule: **identity, the tenancy machinery, and singleton runtime
resources are global; everything a community owns or produces is tenant-scoped.**

## The model at a glance

| Concern | Where | Notes |
|---|---|---|
| Tenant context | [`application/tenant_context.py`](../../application/tenant_context.py) | Request-time `tenant_id` in a `ContextVar` + per-client fallback |
| Addressing | [`middleware/tenant.py`](../../middleware/tenant.py) | Path mode `/t/<slug>`; rewrites the ASGI scope |
| Resolution & CRUD | [`TenantService`](../reference/services.md#tenant_servicepy--tenantservice) | Cached slug/guild/domain lookup; `/platform` CRUD |
| Read scoping / write stamping | [`_tenant.py`](../../application/repositories/_tenant.py) | `scoped(qs)` and `current_tenant_id()` used by every scoped repo |
| Schema | [data-model.md § Multitenancy](../reference/data-model.md#multitenancy) | `Tenant`, `TenantMembership`, a `tenant` FK on every scoped model |
| Roles & membership | [`AuthService`](../reference/authentication.md) | Per-tenant roles; global `SUPER_ADMIN`; tenant-scoped authorization |
| Platform surface | [`pages/platform.py`](../../pages/platform.py) | `/platform` super-admin tenant management |

## Addressing: path mode

Every tenant is reachable at `https://<PLATFORM_HOST>/t/<slug>/…`. `TenantMiddleware`
resolves `<slug>` to a `Tenant`, sets the tenant context, and **rewrites the ASGI
scope**: `/t/<slug>` is stripped from `scope['path']` and appended to
`scope['root_path']`. So the app's single set of unprefixed routes (`/admin`,
`/equipment`, …) serve both the tenant and the platform surface, while redirects
and links built from `root_path` keep the `/t/<slug>` prefix.

- **Unknown or inactive slug → 404.** The request is marked
  (`UNKNOWN_COMMUNITY_SCOPE_KEY`) and left unrouted with no tenant bound, so the
  themed 404 handler renders "Community not found" with a **See all communities**
  button to the picker. (It used to be a bare `Response` with no `Content-Type`,
  which Chrome treated as a file download.)
- **No `/t/` prefix → no tenant context.** That is the *platform surface*: the
  landing/community-picker page, `/platform`, and the shared OAuth callbacks.
- **Excluded (tenant-agnostic) paths:** `/_nicegui`, `/static`, `/api`, `/sw.js`.
  The REST API derives its tenant from the bearer token; websocket/asset traffic
  carries no tenant of its own. NiceGUI addresses its static files and socket.io
  under the page's `root_path`, so a tenant page requests `/t/<slug>/_nicegui/...`;
  the middleware strips the prefix and leaves `root_path` **empty** for those
  requests, since a non-empty one makes NiceGUI's static route 404 and every
  tenant page render blank.
- **NiceGUI prefixing is automatic.** Because the page is served with the
  `root_path` prefix, NiceGUI prepends it to every absolute-path `ui.link` /
  `ui.navigate.to('/…')` client-side — so in-app navigation stays within the
  tenant with no per-call plumbing. Server-side `RedirectResponse`s do **not** get
  this, so those are prefixed explicitly with `root_path`.

### Host mode: a tenant's custom domain

When a request's normalized `Host` matches a tenant's `domain`, `TenantMiddleware`
resolves that tenant with the scope **left untouched** (`root_path` stays empty —
the host owns everything) and marks the request *host mode*
(`application/tenant_context.is_host_mode`). `X-Forwarded-Host` is honored only
behind `TRUST_FORWARDED_HOST` (last value).

- **One domain, one tenant.** Host mode is authoritative there: a stray
  `/t/<other>` stays literal and 404s. Path mode stays authoritative on
  `PLATFORM_HOST`.
- **Resolution caches are bounded.** `TenantService`'s slug and domain caches are
  per-process, dropped wholesale on any write, and FIFO-capped (`_CACHE_MAX`). The
  slug cache stores misses keyed on the attacker-controlled URL slug, so the cap
  is load-bearing; the domain cache keeps hits and misses in separate pools with a
  small miss pool, so a scanner flooding random `Host` values can't evict the real
  custom domains.
- **Discord login runs on the custom domain** — the `redirect_uri` is built from
  the tenant's stored `domain` (https-forced), so the host-only session cookie
  lands on the right host.
- **Profile identity links** (Challonge player-link / Twitch / racetime) complete
  their callback on the platform host. With `HOST_OAUTH_MODE=handoff` they use the
  same signed handoff as login: `/<provider>/link` → platform-host
  `/oauth/link/start` → provider OAuth → a callback minting a token that carries
  only the public provider identity → custom-domain `/oauth/link/claim`, which
  records the link where the session and tenant live (`pages/_oauth_link.py`).
  `/oauth/link/claim` refuses in path mode, where no token can have been minted.
  A handoff the platform host gives up on hands back through that same claim
  route with an opaque `r=<reason>`, because the platform host cannot write a
  notice into the target domain's session. With handoff **off**, the row shows
  "Main site only" as a link to the platform-host equivalent of the profile page
  (built by `platform_link_redirect`) rather than naming a place and staying put.
  Drivable end to end under a provider mock — see
  [development.md](../development.md#driving-the-cross-host-link-handoff).
- **A session belongs to one host.** Every host shares the signing secret, so
  `AuthMiddleware` binds a signed-in session to the host it's first used on
  (`session_host` in `app.storage.user`) and signs it out if the cookie shows up
  on another. Whoever runs a custom domain's DNS or TLS can read its cookies;
  without the binding they could replay one on the platform host as that person
  everywhere, a super-admin included.
- **Privileged flows stay main-site-only** — Challonge STAFF service-connect and
  Discord-connect hold real per-tenant OAuth tokens, so their buttons are hidden
  on a custom domain and their initiation routes bounce to the path-mode surface.

## Tenant context

[`tenant_context.py`](../../application/tenant_context.py) is the primitive the
whole layer reads. It is a peer of `application/services` (import-safe from every
layer, including repositories) and resolves the tenant in two tiers:

1. a `ContextVar` — set by `TenantMiddleware` for the HTTP request, and by
   `tenant_scope(id)` for background/no-request code; else
2. the current NiceGUI client's per-connection stash
   (`app.storage.client['tenant_id']`), written at page build while the
   middleware's contextvar was still set. This covers websocket UI event handlers,
   which run *outside* any request.

```python
from application.tenant_context import (
    get_current_tenant_id,   # int | None — contextvar, then client stash
    require_tenant_id,        # int — raises if no tenant in scope (the safety net)
    tenant_scope,             # contextmanager: bind a tenant for a block
    stash_client_tenant_id,   # persist tenant onto the NiceGUI client at page build
)
```

Anything that runs with neither tier — the Discord bot loop, the DM-queue worker,
the event-dispatch worker, the volunteer-reminder loop, `background_tasks` — must
wrap its work in an explicit `tenant_scope(tenant_id)`.

The client stash is **not reliable across a chain of awaited reloads** inside one
UI handler: a mutation that reloaded three tabs read tenant 1, 1, then `None`, and
a `None` tenant makes every feature flag read as off, so a gated tab rendered
"feature not enabled". A handler that awaits several reloads wraps them in a
`tenant_scope(...)` / `event_scope(...)` captured at page build (the Admin →
Qualifiers page is the worked example).

## Query scoping: explicit threading

There is **no auto-scoping ORM manager** — the `scoped(...)` / stamp-on-write
contract every repository follows is stated in CLAUDE.md. The seam it goes
through is [`_tenant.py`](../../application/repositories/_tenant.py), whose
`current_tenant_id()` is `require_tenant_id()`: forgetting either half fails
loudly (`RuntimeError`) instead of querying across tenants, and the leak tests
(below) assert that failure.

Two carve-outs:

- **Nullable-tenant models** — `AuditLog`, `TelemetryEvent`, `UserRole`, and
  `ApiToken`. They filter to the current tenant on read (excluding NULL rows) and
  stamp the ambient tenant on write. NULL means "platform-level": a super-admin
  audit/telemetry row, the global `SUPER_ADMIN` role, or an MCP OAuth token, whose
  hash lookup happens before any tenant context exists.
  `RoomToken` is the deliberate counter-example: it is a credential too, but its
  hash lookup happens **inside** `/t/<slug>/room/…`, where the middleware has
  already bound the community — so `RoomTokenRepository.get_by_hash` is `scoped`
  like any other read, and a token from another community is simply not found
  rather than needing a check someone must remember to write.
- **The tenancy tables** (`Tenant`, `TenantMembership`, `RacetimeBotTenant`) are
  served by cross-tenant repositories that are **never** `scoped` — they answer
  *which* tenant, so they take explicit ids. See
  [data-model.md § Multitenancy](../reference/data-model.md#multitenancy).

## Identity, roles, and membership

**Users are global** (no `tenant` FK; `discord_id` / `challonge_user_id` /
`twitch_user_id` uniques stay global, so one login works everywhere), while
`UserRole` carries a nullable `tenant` and a `(user, role, tenant)` unique.
Because PKs are global, every tenant-aware `AuthService` check adds a `tenant_id`
filter; `SUPER_ADMIN` is the one role that may have `tenant=NULL` and bypasses the
per-tenant gate.

**Membership answers "who is in this community".** `TenantMembership` is the
join between a global `User` and the tenants they belong to, and it carries one
invariant:

> **Holding any role in a tenant implies membership in that tenant.**

Membership is the *wider* set — a member may hold no roles (a player), but
nobody holds a role without being a member. It is enforced where roles are
written (`UserService.grant_role`, `UserService.create_user`,
`TenantService.bootstrap_staff`, the Discord role sync), not by a reconciler.
`SUPER_ADMIN` is the exception: its row carries `tenant=NULL` and it belongs to
no community, so the grant is guarded on the role rather than on whether a
tenant happens to be in scope. `TenantMembershipService.remove_member` defends
the invariant from the other side — it refuses to eject someone who still holds
roles rather than cascading a revoke, and it revokes the person's API tokens
for that community, which hold no role to refuse on. `scripts/backfill_memberships.py` catches
up rows granted before the invariant existed.

Every **per-community person list** joins through it —
`UserService.get_community_people`, which is what the match, equipment and
bracket pickers, the Users tab, `GET /users` and the MCP `list_users` tool all
read. That is the same basis the access gate below checks, so a picker cannot
offer someone the app would turn away at the door. Pickers and name searches
read it through `get_community_people_summaries` (same filters, seven columns
per row as `PersonSummary`): a hydrated `User` per member held the event loop
for about a second on a 2,000-member community. The Admin → Users table is the
one hand-scoped copy, because `UserTableView` takes a queryset rather than a
coroutine. `get_all_users` still answers
"every account on the platform" and has two deliberate callers, both labelled on
screen: `/platform`'s first-admin picker, choosing from every account precisely
because the target community has no members yet, and the checkout dialog's
**Include people outside this community** toggle, for lending to someone who
just walked in. The reserved `System` automation actor is excluded from the
community list — the migration made it a member of everywhere, and it was
measured being offered as a player. It is excluded by **both** its flag and its
sentinel discord id, spelled as a positive filter: written as
`.exclude(discord_id=SENTINEL)` it also drops every SpeedGaming placeholder,
whose discord id is NULL, because `NULL = <sentinel>` is NULL and NOT NULL is
not true.

**Authorization is tenant-scoped *and* membership-gated.** A tenant page runs
four checks in order — tenant → feature → **membership** → role:

| Check | Failure renders |
|---|---|
| A tenant is in scope | 404 "Pick a community", offering the same path inside each active community (a bare `/help` on the platform host is not a tenant page) |
| The feature is live here | The unknown-route 404, word for word — a subsystem the tenant has off is hidden from everyone, member or not, so an unreleased feature never leaks |
| The viewer is a member | the **join door** (`theme/join_page.py`) — not a 403 |
| The viewer holds a required role | 403 |

The membership check is `middleware.auth.enforce_membership`. It renders a door
rather than a 403 on purpose: forbidden-by-role is a dead end, but not-a-member
is a state with a remedy, and the page whose whole job is to offer that remedy
should not open by saying no. It is rendered **in place**, at the URL that was
asked for, so a Discord deep link survives — approve the request, reload, and
you land where the link pointed. `SUPER_ADMIN` bypasses it, exactly as it
bypasses the role gate; it belongs to no community by design. It is deliberately
**not** gated on `Tenant.is_active` — an inactive community is a separate
concern with its own handling.

Two surfaces sit outside it. `@public_page` routes (the spectator bracket views,
event info, help, the room-seeds page) are world-readable and always were. And the tenant home is registered with a
bare `ui.page` — the same function also renders the platform community picker,
which has no tenant and must stay anonymous — so it calls `enforce_membership`
itself rather than getting it from the decorator.

**Membership is acquired five ways:** a staff grant (Users tab → Add Member),
any role grant (the invariant above), an approved **join request**,
**Discord auto-join**, or **open membership**.

**Who can join is one choice**, Admin → Settings → Members → Joining this community, as a
`JoinMode` (`application/services/tenant_membership_service.py`): *anyone who
signs in* (`OPEN`), *Discord server members, and others can ask*
(`DISCORD_OR_REQUEST`), *Discord server members only* (`DISCORD`), *everyone
asks* (`REQUEST`, the default) or *only people staff add* (`STAFF_ONLY`). It is
stored as the three switches described below (`open_membership`,
`discord_auto_join`, `join_requests_enabled`), which the door and services read,
but staff only ever set them together through `set_join_mode`, so no two can
contradict each other. The Discord modes are refused while no guild is linked,
and the radio hides them then.

**Open membership** (`OPEN`) adds any signed-in non-member on the spot:
`enforce_membership` calls `join_openly` before `join_via_discord`, with the same
contract (never raises, closes a pending request, audits `tenant.member_added`
with `source: open`, records `MembershipSource.OPEN`, sticky welcome toast). A
removal or decline doesn't stick against it, and the Remove confirmation and
removal DM say so: to keep someone out, change the mode.
`TenantJoinRequest` is the self-serve enrollment path whose absence used to be
the documented reason no gate existed: one row per `(user, tenant)` — a denied
request is re-opened, never appended to — with the community's staff notified
when it arrives and the requester notified on **both** outcomes. A declined
requester waits `JOIN_REQUEST_COOLDOWN` (7 days) before the door takes another
ask: the door says the date instead of showing the form, `request_to_join`
refuses meanwhile, and the decline DM gives the date (Discord `<t:…:D>`) plus a
**Join the Discord server** button when an invite is set.

Every way in closes a pending request, not only the queue: Add Member (closed
with that staff member as decider, and the person is DM'd that staff added
them), `ensure_member` from a role grant (decider = the granting staff member),
the Discord role sync or the SpeedGaming import (no decider), and auto-join.
`ensure_member` DMs the person only when it closed a request of theirs, since
the door promised a message either way, and returns the closed request so the
caller's audit row records `closed_request_id`. Each closure publishes
`TENANT_JOIN_APPROVED`. Every membership row records its `source`
(`MembershipSource`) and `created_at`, shown on the Users tab as **Joined** and
**Via**, with a "Joined in the last 7 days" filter. Decline refuses for
someone who is already a member, so a leftover row can't tell a member they
weren't approved; Approve still clears it. Removing a member asks for
confirmation by name, DMs them, and withdraws their ADA request (clearing its
details).

**Discord auto-join** is the door's other way through, off by default
(`SystemConfiguration` key `discord_auto_join`, Admin → Settings → Members).
With it on and a guild linked, `enforce_membership` calls
`TenantMembershipService.join_via_discord` for a signed-in non-member before
rendering the door: the bot checks the linked server (`is_guild_member`), and a
member is added on the spot and the page they asked for renders. It grants
membership only, never roles (role mappings still decide those), closes any
pending join request as approved with no decider, and audits
`tenant.member_added` with the user as actor and `source: discord_auto_join`.
Staff aren't DM'd (nothing needs doing); the Users tab marks the member
**Joined via Discord**. The person lands with a sticky welcome toast saying why
they're in (`stash_notice(..., sticky=True, tenant_id=…)`: held for that
community's pages, shown on up to two loads so it survives the first-visit
timezone reload, cleared on **Got it**).

**A removal sticks.** Removing a member records a `TenantMemberBlock`, and
open membership and Discord auto-join both refuse a blocked person, so the
removal holds whatever the join mode. A join request still reaches staff, and
any staff add lifts the block: Add member, an approved request, a manual role
grant, the platform first-admin grant. The Discord role sync is a staff
designation too and still admits. The door tells a blocked person staff removed
them. **A decline doesn't stick against auto-join** (`auto_join_active(tenant_id)`):
a declined requester's door and DM lead with "members of its Discord server get
in automatically" before the date a new web request opens. Without auto-join
the cooldown is the only route back.
It never raises, and a bot that cannot answer counts as "not known to be a
member", so a Discord outage leaves people at the door with its Request access
button rather than on an error page. Leaving the server does **not** remove the
membership. It only runs at the door, so someone in the server who never opens
the community gets no row. A staff-supplied invite (`discord_invite_url`,
validated to a `discord.gg` link) is offered on the door whether or not
auto-join is on.

**Join requests can be switched off** (`join_requests_enabled`, default on,
Admin → Settings → Members → Joining this community → Take join requests; that section
has its own **Save join settings**, so it saves apart from the page-wide
Save) independently of auto-join, so a
community can admit its Discord server and nobody else. With it off the door
drops the message box and Request access button, its copy names only the ways in
that are still open, and `request_to_join` refuses with a `ValueError` (a door
left open from before the switch still has a button). Requests already pending
stay in the staff queue and can still be approved or denied; auto-join still
closes them as it lets their authors in. None of the three is a `FeatureFlag`:
each is one staff toggle.

The gate is a page-level check on a *person*. It does not apply to the REST API
or MCP (a token belongs to a tenant, and wave 2 scoped what those return), to the
Discord bot (an interaction handler acts for someone already assigned), or to
workers (they run in `tenant_scope`, not as a user). The one service that
enforces it directly is **crew signup**, because the measured symptom was a
non-member being offered *Sign Up* — and UI-only gating is not gating. It
checks through `TenantMembershipService.admit`, which lets the person in first
when open membership or auto-join would have, so a Discord signup button
pressed before the site was ever opened isn't refused.

See [authentication.md](../reference/authentication.md) and
[role-based-auth.md](../reference/authentication.md#roles).

## Auth, sessions, and OAuth

Path-mode tenants share one session cookie on `PLATFORM_HOST`, so session state is
handled deliberately:

- **Identity keys stay flat** (`discord_id`, `username`, `avatar`, `authenticated`,
  and the OAuth CSRF/return keys) — one login is shared across tenants, and the
  OAuth callbacks run on the bare platform host with no tenant, so they could not
  read a namespaced key.
- **Tenant-scoped UI state is namespaced** under
  `app.storage.user['by_tenant'][str(tid)]` via
  [`tenant_session.py`](../../application/utils/tenant_session.py) — e.g. the match
  table filters, whose values are tenant-local ids that would be meaningless in
  another community.
- **OAuth (Discord, Challonge, Twitch, racetime.gg)** uses a single registered
  redirect URI per provider on `PLATFORM_HOST` (plus, for Discord login under
  Design A, one per custom domain — see [Host mode](#host-mode-a-tenants-custom-domain)),
  built **at request time** (Discord in [`pages/auth.py`](../../pages/auth.py)) —
  never at import. The tenant return path
  (`/t/<slug>/…`) is captured at initiation (where
  the tenant is in scope) and stored in the session; the shared callback — which
  lands on the bare host — reads it and navigates back into the originating
  community. [`tenant_urls.py`](../../application/utils/tenant_urls.py) holds the
  pure `sanitize_return_path` logic that rejects cross-tenant and auth-route
  referrers.

**REST API:** `TenantMiddleware` skips `/api`; instead `api/dependencies.py`
resolves the PAT by hash (no tenant yet), then sets the tenant context from the
token's `tenant_id` for the duration of the request and resets it in a `finally`.

## Platform surface

Served on the bare `PLATFORM_HOST` with **no** tenant context:

- **`/` (community picker)** — [`pages/home.py`](../../pages/home.py) lists active
  tenants (each linking to its `/t/<slug>/` home) plus, for a super-admin, a link
  to `/platform`. It carries the same login/logout control as the tenant
  `BaseLayout`, so identity — which is global — can be established before picking a
  community.
- **`/platform`** — [`pages/platform.py`](../../pages/platform.py), gated on
  *no tenant context* **and** `is_super_admin`. Tenant CRUD (name, slug, domain,
  guild id, active), the **first-admin grant** (below), a **Setup** column, a
  per-tenant **Features** dialog (tri-state flag overrides), **Racetime Bot CRUD +
  per-tenant authorization grants** (`RacetimeBotService`; client secrets are
  write-only and never shown), the **Feature Groups** tiers
  ([feature-flags.md](feature-flags.md)), and the platform-wide **Service Health**
  board, one tab each (`?tab=` opens one). The tabs are split across `pages/platform_bots.py`,
  `platform_feature_groups.py` and `platform_tenant_admins.py`. Its queries pass explicit ids
  (intended cross-tenant capability), backed by `TenantService` /
  `RacetimeBotService`, whose CRUD/grant methods are super-admin-gated and
  audited as platform-level rows (`tenant=NULL`).

## Provisioning a community

Creating a tenant writes exactly one row — no starter tournament, no stream
room. What it needs next is a *first admin* and an order to work in:

| Step | Where |
|---|---|
| Grant the first `STAFF` + `TenantMembership` | `/platform` → a tenant's **Admins** action (`TenantService.bootstrap_staff`), offered automatically at the end of creation. `scripts/seed_tenant.py --operator-discord-id` does the same from a shell. |
| Everything after that | The **Setup** tab on the community's `/admin` |

`TenantSetupService` derives the checklist on every read — five existence
checks, nothing stored. Three steps are **required** (a staff member, a
tournament, an enrolled player: a match cannot be scheduled without all three);
a stage and an event window are shown but advisory, because a match
schedules without either. A community that deletes its last tournament becomes
un-set-up again and the checklist says so.

While a community is not ready, `build_admin_tabs`
([`pages/admin.py`](../../pages/admin.py)) prepends a `Setup` tab, which
`BaseLayout` then makes the landing view; once the required steps are done the
tab is not built at all. The same derivation feeds `/platform`'s Setup column
(`Ready`, or `1 of 3` with a tooltip naming what is outstanding) via
`TenantSetupService.status_for(tenant_id)`, which wraps `tenant_scope` because
the platform surface has no ambient tenant — the same shape as
`TenantService.list_staff`.

## Discord: one bot, many guilds

A single bot serves every community. `discord_guild_id` is **not unique**, so a
Discord server may back several tenants; `TenantService.list_tenants_for_guild(guild_id)`
resolves *every* tenant linked to an incoming guild and the callers fan out over
them (an unknown guild resolves to an empty list and is ignored):

- **Role sync** — `DiscordRoleMappingService.sync_user_roles` (at login) and the
  live `GUILD_MEMBER_UPDATE` handler (`sync_member_roles`) both fan over every
  tenant sharing the guild, wrapping each per-tenant sync in `tenant_scope`.
  Mappings are tenant-scoped (`DiscordRoleMapping.tenant`), so a shared server's
  mappings stay separated and the same Discord role can grant different app roles
  per community.
- **Interaction handlers** (`crew_signup`, `crew_acknowledgment`,
  `match_acknowledgment`, `match_hard_preset`, `reschedule_agreement`,
  `volunteer_acknowledgment`, `watch_buttons`) resolve the tenant from the
  referenced entity's `tenant_id` (helpers in `discordbot/_tenant.py`) and wrap DB
  work in `tenant_scope`.
- **`volunteer_reminder`** does one cross-tenant scan over a wide window, then
  re-checks each assignment against **its own tenant's** lead-time
  `SystemConfiguration` inside `tenant_scope`.

### Linking a tenant to a server (verified)

Because `discord_guild_id` is not unique, a tenant may not simply *claim* a guild
id — that would let one community sync roles against another's server. Linking is
gated twice, by [`DiscordLinkService`](../reference/services.md):

- **App gate** — only tenant `STAFF` (or a global super-admin) may connect or
  disconnect (`can_manage_link`).
- **Discord gate** — the acting user must administer the target guild. The
  "Connect Discord server" button (Admin → Settings → Discord) runs Discord's
  **bot-authorization** OAuth flow (`scope=bot`), which Discord only lets a user
  with *Manage Server* complete and which adds the bot if it is absent. The
  callback (`/oauth/discord/connect/callback`, on the platform host) exchanges the
  code for an authoritative `guild` object, then **re-checks server-side** that
  the acting user has Manage Server / Administrator / is owner of that guild
  (`DiscordService.member_can_manage_guild`, which fails closed) before stamping
  `Tenant.discord_guild_id`. No client-supplied guild id is ever trusted.

The super-admin `/platform` surface keeps a raw guild-id field as a break-glass
override. `MOCK_DISCORD` skips the OAuth round-trip and links a mock guild.

## Events, webhooks, telemetry

- `Event.create()` snapshots `tenant_id` from the ambient context (NULL for
  platform events) and includes it in `to_wire()`.
- The dispatch worker runs outside any request, so `bus.publish` wraps each async
  subscriber's coroutine in `tenant_scope(event.tenant_id)` (`_scoped`) so
  subscribers can safely use scoped services.
- The **webhook subscriber** delivers only to the event's tenant's `Webhook` rows;
  the **telemetry mirror** stamps `TelemetryEvent.tenant` from the event, and
  analytics aggregates scope by tenant. See [webhooks.md](webhooks.md),
  [telemetry.md](telemetry.md), [event-system.md](event-system.md).

## Challonge (per-tenant)

`ChallongeConnection`, `ChallongeParticipant`, `ChallongeMatch`, and
`ChallongeApiUsage` are tenant-scoped ("newest connection row per tenant is
authoritative"; usage tallied per `(tenant, period)`), so each community has its
own service account and quota. Player identity (`User.challonge_user_id` /
`twitch_user_id`) stays global — a one-time capture for the person.

## Migration and bootstrap

The `tenant`-adding migration is **additive** — schema-add (nullable FK + new
composite uniques), data backfill (one `default` `Tenant`, every scoped table
updated, a `TenantMembership` per existing user), then constraint-tighten
(`SET NOT NULL`, drop the superseded single-column uniques) in one migration. See
[data-model.md § Migrations](../reference/data-model.md#migrations).

Bootstrap and ongoing tenant management run through `scripts/grant_super_admin.py`,
`scripts/seed_tenant.py` and `scripts/grant_staff.py` — invocations in
[deployment.md](../deployment.md#tenant-and-role-bootstrap) — though the first
STAFF grant is now reachable from `/platform` itself. `scripts/seed_dev.py` seeds
two fully-provisioned tenants plus `fledgling`, deliberately stopped after its
staff grant so the setup checklist is visible in a half-done state.

## Testing and avoiding leaks

- An autouse fixture binds a default tenant (id 1) for every test; the `db`
  fixture creates that `Tenant` and wraps scoped models' `.create` to stamp it, so
  existing test sites need no per-call plumbing. The wrapper lives only in the
  harness — production never auto-stamps.
- **Leak/isolation tests** (`tests/tenancy/test_tenant_isolation.py`,
  `test_tenant_read_isolation.py`, plus the per-domain `*_tenant_isolation.py`
  modules) create data under two tenants in explicit `tenant_scope` blocks and
  assert each sees only its own rows — and that a scoped operation with no tenant
  raises.
- `test_tenant_middleware.py` drives a real Starlette app to prove the scope
  rewrite routes correctly; `test_tenant_urls.py` / `test_tenant_session.py` cover
  the return-path and session-namespacing logic.

The checklist for adding a tenant-scoped model is in CLAUDE.md § Multitenancy.
