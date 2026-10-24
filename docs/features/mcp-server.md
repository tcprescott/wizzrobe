# MCP server

Wizzrobe serves a remote **MCP** (Model Context Protocol) endpoint at `/mcp`, so
organizers can connect Claude Desktop or Claude Code to their communities and
ask questions across tournaments, matches, people, and history.

The surface is **OAuth-only**, and **read-only unless the person connecting asks
otherwise**. It is a fourth entry surface alongside the web UI, the REST API, and
the Discord bot: it calls services, never repositories, and holds no business
logic of its own.

| | |
|---|---|
| Endpoint | `POST/GET/DELETE {BASE_URL}/mcp` |
| Transport | Streamable HTTP, **stateless**, JSON responses (no SSE on POST) |
| Auth | OAuth 2.1 (RFC 7591 dynamic registration + PKCE). **Personal access tokens are refused.** |
| Writes | 21 tools (19 match-management, plus tournament self-signup and withdrawal), served only to a connection the consent screen approved for writing. Everything else is a read annotated `readOnlyHint`. |
| Reads | 57 tools. Each mirrors the gate of its REST counterpart in `api/routers/`. |
| Feature flag | None — the server is always on. `MCP_ENABLED=false` is an operational kill switch. |
| Rate limit | Shares `/api`'s buckets and `API_RATE_LIMIT_PER_MIN` (default 120). Covers `/mcp` **and** the unauthenticated authorization-server routes (`/authorize`, `/token`, `/register`, `/revoke`, and the AS metadata document). |
| Code | [`mcpserver/`](../../mcpserver), consent page [`pages/mcp_consent.py`](../../pages/mcp_consent.py) |

## Connecting a client

**Claude Desktop** — Settings → Connectors → Add custom connector, URL
`https://<your-host>/mcp`. It discovers the authorization server, opens a browser
to sign in with Discord, and shows a consent screen.

**Claude Code** — `claude mcp add --transport http wizzrobe https://<your-host>/mcp`,
then `/mcp` to complete the browser flow.

**MCP Inspector** — `npx @modelcontextprotocol/inspector` against the same URL;
the fastest way to see every tool's schema and drive the OAuth flow by hand.

Users find the URL on their profile page, under **API tokens & AI clients**, and
disconnect a client from the same list.

## Authentication

### Why not personal access tokens

A PAT presented to `/mcp` gets **401** with
`WWW-Authenticate: Bearer resource_metadata="…"`, not 403 — the challenge is what
lets a compliant client discover the authorization server and start the flow,
where an accurate 403 would leave it nowhere to go. The refusal is mutual: an
OAuth token presented to `/api` is refused too, so a credential minted for one
surface can never be replayed against the other.

### The two token kinds

Both live in `ApiToken`, distinguished by `origin` — one table so revocation,
expiry, and the profile listing have a single implementation.

| `origin` | `tenant` | Surface | Scope |
|---|---|---|---|
| `pat` | set | `/api` | one community, unchanged from before |
| `oauth` | `NULL` | `/mcp` | platform-wide; each tool call names its community |

OAuth access tokens live 1 hour and carry a 30-day refresh token. Refreshing
rotates **both** halves, so a leaked refresh token is single-use: the theft
surfaces as the attacker's next call failing rather than as silent parallel
access. Rotation re-keys the same row, so `scope` and `read_only` survive it —
a refresh can never widen a grant.

### Scopes

| Scope | Granted | Effect |
|---|---|---|
| `wizzrobe:read` | always | The read tools. |
| `wizzrobe:write` | only when the consent box is ticked | The write tools, and `read_only=False` on the token. |

A dynamically-registered client is registered with **both**, so a client that
asks for write is not rejected at `/authorize` — but asking is not getting.
`McpAuthService.granted_scope` derives the granted scope from the human's answer
alone and ignores what the client requested, which is what keeps the box on the
consent screen from being decoration.

### Discovery

- `/.well-known/oauth-protected-resource` (and the `/mcp`-suffixed form) — RFC 9728.
- `/.well-known/oauth-authorization-server` — RFC 8414, from the MCP SDK.

Both are served at the **origin root** and need no credential. The SDK's helper
would register them inside the MCPServer sub-app, where no client looks, so
`mcpserver/wellknown.py` registers them on the outer app instead.

### Clients are public

Registration is open (RFC 7591) and every client is stored and reported to the
SDK as `token_endpoint_auth_method: none`, whatever it asked for. Claude's
connectors register with `client_secret_post` (and the SDK defaults a request
that names no method to the same), so the SDK mints a secret and echoes it back;
the client may keep sending it, and it is never checked. PKCE is what binds a
code to its client. Reporting the requested method would break the flow: mcp 2.x
refuses a secret-based client with no stored secret, which surfaced as a 401 on
every `/token` call (`WizzrobeOAuthProvider.get_client`).

### Consent

`pages/mcp_consent.py` at `/oauth/mcp/consent` is a bespoke `@ui.page`, not a
`@protected_page`: every `@protected_page` is a *tenant* page and 404s without a
tenant, but an MCP grant is platform-wide — it authenticates a person, not a
membership. It registers itself in `protected_routes` directly to get the
sign-in redirect. There is **no community picker**; the token carries no
community, so asking would imply a scoping it does not have.

It still reads as Wizzrobe: the page applies
[`render_platform_chrome('Authorize')`](../reference/frontend.md#tenant-less-chrome-themechromepy)
— the shared tenant-less header, stylesheet, phoenix palette and dark mode — and
its card, grant list and expired-transaction notice use the `.consent-*` classes
in `styles.css`. The palette is the shipped default, not a tenant override,
because the credential is not scoped to a community.

**Which redirect URIs register.** `https://` anywhere, `http://` only to `localhost`, `127.0.0.1` or `[::1]`, or a native app's own scheme (RFC 8252). `javascript:`, `data:`, `vbscript:`, `file:`, `blob:` and URIs with a fragment are refused with `invalid_redirect_uri`, because the consent page sends the signed-in browser to that URI from the app's own origin. `approve` and the deny button re-check, for clients stored before the rule. Code exchange and refresh rotation are single conditional UPDATEs, so two concurrent requests can't both spend one code or one refresh token.

**The redirect target.** Under the lede, the card names the host approving will
send the code to. Registration is open, so `client_name` is a string the
requester chose — anyone can register a client called "Claude". The redirect URI
is the part that cannot be faked, because it is where the authorization code
goes, and a person who can see `claude.ai.evil.test` on the card can decline. The
host is rendered by `_redirect_target()` (host and port for `http(s)`, the whole
URI for a native client's custom scheme, which has no netloc).

**The write box.** One unticked checkbox, "Let it make changes". It is the only
place write access can be granted: a client cannot request its way past it and a
refresh cannot widen a grant without it, so raising a read-only connection to a
writing one means reconnecting. Ticking it redraws the grant list — the two
write lines appear and the closing note flips from "it cannot change anything" to
"it acts as you" — because a card that lists writes while still promising
read-only is worse than either half alone. The connection's kind is visible
afterwards on the profile's credential list, where a writing grant carries a
`can make changes` badge and a read-only one carries none.

**The typed confirmation.** Ticking the box also reveals a warning banner and a
field asking for the phrase `this is dangerous` (`WRITE_CONFIRMATION_PHRASE`);
Approve stays disabled until it matches, with case and spacing forgiven.
`McpAuthService.approve(..., allow_write=True)` re-checks it and raises
`ValueError` on a mismatch *before* consuming the pending request, so a typo
leaves the screen usable and no other caller can grant write without it.

## Choosing a community

Every tool except `whoami`, `list_tenants`, `server_version` and the platform tools (`runtime_stats`, `platform_*`) takes a required `tenant` argument
— a community slug. `list_tenants` returns the slugs the caller may use.

**Membership floor.** A tool reaches a community only where its user belongs —
a member, or a role holder (super admins excepted) — parity with the REST
surface, where a token is bound to one community and can never address another.
`list_tenants`/`whoami` list exactly those communities (`belongs_to_tenant` in
`mcpserver/auth.py`); a plain member shows `roles: []`.

An unknown slug and an entitled-elsewhere slug produce **byte-identical**
`not_found` errors, so error text cannot be diffed into a directory of the
communities hosted here.

## Authorization

Each tool mirrors the gate its REST counterpart uses. `mcpserver/registry.register`
is the only way to attach a tool to the server, so none can ship ungated —
`tests/mcp/test_mcp_catalogue.py` asserts the served list and the gate registry
agree.

| Gate | Requirement | REST equivalent |
|---|---|---|
| `GLOBAL` | a live token; names no community | — |
| `PLATFORM` | the global `SUPER_ADMIN` role; names no community; **listed only to a super admin** | `require_super_admin` |
| `ACTOR` | a live token + membership of the named community | `require_api_actor` |
| `STAFF` | `AuthService.is_staff` | `require_staff` |
| `ADMIN` | `AuthService.can_view_admin` | `require_admin` |

The feature flag is checked **before** the role, matching `@protected_page`: a
subsystem the community has not enabled is hidden from everyone, staff included,
and answers `not_found` — a `forbidden` would confirm the feature exists. It runs
*after* the membership floor, though: someone who doesn't belong to a community gets the
floor's "No community" answer whatever tool they call, so a feature-gated tool
can't be used to learn which slugs exist.

**The read-only refusal is per tool, never per connection.** Read-only is the
consent screen's default and most connections carry it, so the REST API's shape
— reject the token outright — would refuse nearly every legitimate caller here.
`authorize()` instead refuses only the tools registered `write=True`, and only
after the feature and membership checks have run, so a read-only token learns no
more about a community than a writing one would.

Order within `authorize()`, and why: **feature → membership floor → connection →
role**. The connection check sits below the membership floor because a
`forbidden: read-only` for a community the user cannot see would disclose that
the community exists. It sits above the role check because the two refusals
call for different fixes — reconnect with the box ticked, versus ask someone for
a role — and the connection one is both cheaper and the common case.

## Tool catalogue

`tenant` is required on every tool below except the two orientation tools and the platform tools.

### Orientation
| Tool | Gate | Notes |
|---|---|---|
| `whoami` | GLOBAL | Identity, roles, enabled flags per community, **and `can_write`**. The cheapest way to orient. |
| `list_tenants` | GLOBAL | The slugs every other tool needs, with the same roles/flags. |
| `server_version` | GLOBAL | Which build is running: version, commit, commit date, source link. The server's MCP handshake (`serverInfo.version`) carries the same version. |

Both report each community's live feature flags. A flag is not a permission, so
it goes to everyone who can see the community — and reporting it lets a client
skip a tool instead of discovering its `not_found` a round-trip later.
`can_write` is a property of the *connection*, not the person: the same user can
hold a writing grant in one client and a reading one in another.

### Tournaments and matches
| Tool | Gate | Flag |
|---|---|---|
| `list_tournaments`, `get_tournament` | ACTOR | — |
| `list_tournament_signups` | ACTOR | — |
| `list_tournament_players` | ACTOR (staff or the tournament's admins) | — |
| `get_tournament_payouts` | ACTOR | `PAYOUTS` |
| `list_stages`, `get_stage` | ACTOR | — |
| `list_matches`, `get_match`, `get_schedule` | ACTOR | — |
| `list_unbooked_matches` | ACTOR (staff, or the tournament's admins) | — |
| `suggest_match_time` | ACTOR | — |
| `match_operations_report` | ADMIN | — |
| `my_reschedule_requests` | ACTOR | — |

`get_tournament_payouts` returns one tournament's prize split with each place's
amount computed from the pool as it stands. `PayoutService` re-gates it on staff
or that tournament's admin, so it is a read the connection's own authority has to
earn. `set_tournament_prize_pool` and `set_tournament_payout_split` are its writes
(see [Writes](#writes)).

`list_tournament_players` is the roster Manage players shows, for staff or that
tournament's admins.

`list_unbooked_matches` is Match Schedule's "Not booked yet" view: open
Challonge, start.gg and native bracket matchups with no game booked. It reads
`bracket_sources.list_unbooked`, so it follows the same scope (staff see the
community, a tournament admin their own tournaments, anyone else nothing) and
skips a source whose flag is off. The totals and per-tournament counts cover
every matchup; the rows stop at `limit`.

`list_tournament_signups` reports the caller's own signup state per tournament,
including `can_sign_up` / `can_withdraw`. Those two booleans are the service's
own answer rather than something to re-derive from the window: a model reasoning
from the dates alone would offer a signup that a started Challonge bracket, an unlinked Challonge account or
the caller's existing enrolment already rules out.

`suggest_match_time` mirrors `GET /tournaments/{id}/match-suggestion`. It reads
rather than writes — it books nothing — but it is what makes the scheduling
writes usable from a model, which would otherwise have to guess a slot and
discover the clash after creating the match.

### People, crew, volunteers
| Tool | Gate | Flag |
|---|---|---|
| `get_user` | ACTOR (self, or staff for anyone else) | — |
| `get_member_details` | STAFF | — |
| `get_player_availability` | ACTOR (self, or staff for anyone else) | — |
| `list_users` | STAFF | — |
| `list_match_crew`, `crew_coverage_report` | ADMIN | — |
| `list_volunteer_positions`, `list_volunteer_shifts`, `volunteer_coverage` | ACTOR (service: `can_view_volunteer_schedule` — the Volunteer hub's roles plus coordinators; drafts hidden from non-coordinators) | `VOLUNTEERS` |
| `volunteer_hour_trends` | VOLUNTEER_ADMIN | `VOLUNTEERS` |
| `my_volunteer_shifts` | ACTOR | `VOLUNTEERS` |
| `list_join_requests` | STAFF | — |

The volunteer tools sit one gate **above** their REST counterparts
(`require_api_actor`), deliberately: REST reads back a self-service view of your
own shift, whereas one call here returns a whole window's roster with names.
`my_volunteer_shifts` is that self-service view, at ACTOR: your own published
shifts and nobody else's, the same as `GET /volunteers/me/assignments`.
`volunteer_hour_trends` is served by `AnalyticsService`, which carries no flag of
its own, so `VOLUNTEERS` is declared at the tool — without it a community with
volunteers off could still read its volunteer hours here.

`list_match_crew` includes **unapproved** signups, which is why it is ADMIN while
`get_match` is ACTOR — `get_match` reuses the REST serializer and inherits its
rule that unapproved crew are never disclosed.

### Reports
| Tool | Gate |
|---|---|
| `capacity_forecast`, `stage_utilization` | ADMIN |
| `tournament_health`, `crew_participation_trends`, `activity_trends` | ADMIN |
| `matches_active_at` | ADMIN |

ADMIN mirrors the Admin → Reports tab (`is_staff or tournament-admin or
crew-coordinator`, which is what `can_view_admin` answers).

Two are **reshaped rather than passed through**, because `ReportsService` builds
its payloads for a chart (per-interval and per-match detail; `stage_utilization`'s
even contains ORM `Match` rows): `capacity_forecast` returns peaks and headroom
instead of the full series, `stage_utilization` per-stage totals instead of
every booking.

### Exports
| Tool | Gate |
|---|---|
| `list_my_exports`, `get_export` | ACTOR |
| `export_tournaments`, `export_volunteer_data` (write) | ACTOR, re-checked in `DataExportService` |

`get_export` returns the export and, once a tournament export is ready, its parsed
`tournaments.json` document, so a client can read results without downloading a
ZIP. See [data-exports.md](data-exports.md).

### Equipment
| Tool | Gate | Flag |
|---|---|---|
| `list_equipment` | ACTOR (service: `can_manage_equipment`) | `EQUIPMENT` |
| `get_equipment` | ACTOR | `EQUIPMENT` |
| `my_equipment_checkouts` | ACTOR | `EQUIPMENT` |

Mirrors `api/routers/equipment.py`. The inventory carries no role check at all:
every member of a community with the feature on may read it and see its current
holders. That was the **home** Equipment tab until Home came down to four tabs;
the read is unchanged, it is just no longer browsable from a player surface. These were ADMIN when
there was no REST counterpart, on the belief that the *admin* equipment tab was
the gate of record; it is not, and the stricter gate only meant a member saw
less through a connected client than in their own browser. `private_notes` is
the one genuinely staff-only field and is absent from both shapes.

### Check-in desk
| Tool | Gate | Flag |
|---|---|---|
| `list_check_in_events`, `get_check_in_roster` | ACTOR | `EVENT_CHECK_IN` |

Mirrors `GET /check-in/events` and `/events/{id}/roster`. `CheckInDeskService`
answers who may read: staff and Check-in Desk volunteers, and a desk volunteer
sees only open events. The roster takes `search` and `not_checked_in_only` and
returns at most 200 rows, because a whole venue's roster is not something a
model needs in one call to find one person.

### Observability
| Tool | Gate |
|---|---|
| `list_audit_log` | STAFF |
| `list_sent_messages` | STAFF |
| `telemetry_summary`, `telemetry_top`, `get_system_config` | STAFF |
| `list_service_health`, `list_webhooks`, `list_webhook_deliveries` | STAFF |
| `list_notification_settings` | STAFF |
| `my_notification_settings` | ACTOR (your own) |
| `list_feedback` | STAFF | `FEEDBACK` |
| `runtime_stats` | PLATFORM (SUPER_ADMIN, checked again in `RuntimeStatsService`) |
| `memory_census` | PLATFORM (SUPER_ADMIN, checked again in `RuntimeStatsService`) |

`runtime_stats` is the live load on the process (event-loop lag and stalls,
CPU, memory, connected tabs, DB pool, worker timing; see
[observability.md](observability.md#live-load-runtime_stats)). It names no
community because the figures cover all of them, which is also why a community
role cannot read it. `memory_census` counts what the heap holds by type, plus
NiceGUI pages and elements still in memory and live-channel subscribers, for
when `runtime_stats` shows memory climbing. It walks every tracked object, so
each call freezes the app about as long as a full collection.

`list_webhooks` never returns the signing secret, and `list_service_health` is
the **tenant subset** only — the platform-wide board is `platform_service_health`.
`list_notification_settings` mirrors `GET /api/notification-settings`: every
message once per kind of recipient, with this community's mode and the default
(see [notification-settings.md](notification-settings.md)).

### Platform (super admin)
The `/platform` page's tabs, in `mcpserver/tools/platform.py` (reads) and
`platform_writes.py` (writes). Every tool is `Gate.PLATFORM`: it requires the
global `SUPER_ADMIN` role, takes no `tenant`, and `WizzrobeMCP.list_tools` hides
it from everyone else (a community's Staff is an outsider here). Each mirrors a
REST endpoint under `/api/platform`, `/api/racetime-bots` or
`/api/service-health`, and the owning services check the role again.

Tools about one community take its slug as `community`, resolved by
`PlatformAdminService.find_community`, which **includes switched-off
communities** — the `tenant` argument hides those, and a super admin has to
reach one to switch it back on.

| Reads | Writes |
|---|---|
| `platform_list_communities`, `platform_get_community` (setup, Staff, every feature's availability) | `platform_create_community`, `platform_update_community`, `platform_set_community_active`†, `platform_grant_community_staff` |
| `platform_list_feature_groups` | `platform_set_community_feature_group`, `platform_set_community_feature` (`inherit`/`on`/`off`), `platform_create_feature_group`, `platform_update_feature_group`, `platform_delete_feature_group`† |
| `platform_list_users`, `platform_get_user` | `platform_set_user_active`† |
| `platform_list_racetime_bots` (grants as slugs; never the secret) | `platform_update_racetime_bot`, `platform_grant_racetime_bot`, `platform_revoke_racetime_bot`†, `platform_restart_racetime_bot` |
| `platform_list_log_levels` | `platform_set_log_level`, `platform_reset_log_level` |
| `platform_service_health`, `runtime_stats` | `platform_refresh_service_health` (never alerts) |

† destructive. **Left out on purpose** (Thomas, 2026-10-16): setting or removing
a password, creating an account, and anything that takes a racetime bot's client
secret (creating a bot) — a credential typed into a chat lands in a transcript.
Deleting a bot is left out too, since only the page can make one again.

`list_feedback` mirrors `GET /api/feedback` and carries that router's flag.
`FeedbackService` refuses a community with the feature off regardless, so the
declaration changes no answer — it changes *when* the answer arrives, by hiding
the tool from the listing instead of letting a model plan around a refusal it
discovers a round-trip later.

### Online play
| Tool | Gate | Flag |
|---|---|---|
| `list_presets`, `get_preset`, `list_randomizers` | ACTOR | — |
| `list_triforce_texts` | ACTOR | `TRIFORCE_TEXTS` |
| `list_race_room_profiles`, `get_race_room` | ACTOR | `RACETIME_ROOMS` |
| `list_race_rooms` | ACTOR (service: Staff or Sync admin) | `RACETIME_ROOMS` |
| `list_speedgaming_links`, `list_speedgaming_episodes` | ACTOR | `SPEEDGAMING_ETL` |

`list_randomizers` is the peer of `GET /seeds/randomizers`: it filters on the
credentials the community has configured, not on a flag, so it answers what a
preset may actually name rather than the full catalogue.

`list_triforce_texts` is registered at ACTOR and does its own check — staff, or
an admin of *this* tournament — because the gate it mirrors is per-tournament
and neither ADMIN nor STAFF expresses it. `TriforceTextService` does not re-gate
the read, so a bare ADMIN registration would have handed the moderation queue to
any crew coordinator.

Several sit at ACTOR because the *service* carries the real check
(`can_manage_presets`, `ensure_can_manage_sync`); mirroring the router's gate
rather than inventing a stricter one keeps both surfaces answering the same
question. `list_race_rooms` follows the same shape: the service's
`list_open_rooms_for_current_tenant` lets Staff and Sync admins through.

### Feature-gated competition
| Tool | Gate | Flag |
|---|---|---|
| `list_brackets`, `get_bracket`, `list_bracket_matches` | ACTOR | `BRACKETS` |
| `list_bracket_entrants`, `get_bracket_standings` | ACTOR | `BRACKETS` |
| `list_async_qualifiers`, `get_async_qualifier` | ACTOR (admins get every qualifier they run and its pools; members get the open ones, without pools, as `/qualifiers` shows them) | `ASYNC_QUALIFIERS` |
| `get_async_qualifier_leaderboard` | ACTOR | `ASYNC_QUALIFIERS` |
| `list_async_qualifier_live_races` | ACTOR | `ASYNC_QUALIFIERS` |

The bracket tools read through `BracketService`'s viewer-aware reads with the
caller as viewer: a DRAFT or CANCELLED stage, and the roster of a tournament
with nothing published, are `not_found` to anyone but staff — the same rule as
the public pages and REST ([brackets.md](brackets.md#public-access--anonymous-and-reachable)).

`list_bracket_matches` resolves entry ids to entrant names from two list reads
rather than prefetching per match: a fixed pair of queries covers the whole
field, whatever its size.

## Writes

Sixty-nine tools, served only to a connection approved for writing: twenty-one in
[`mcpserver/tools/match_writes.py`](../../mcpserver/tools/match_writes.py), nine in
[`mcpserver/tools/event_writes.py`](../../mcpserver/tools/event_writes.py), thirteen in
[`mcpserver/tools/tournament_writes.py`](../../mcpserver/tools/tournament_writes.py),
twenty-four in [`mcpserver/tools/admin_writes.py`](../../mcpserver/tools/admin_writes.py),
plus `sign_up_for_tournament` and `withdraw_from_tournament`, which live beside the
other tournament tools in
[`mcpserver/tools/tournaments.py`](../../mcpserver/tools/tournaments.py).
The super-admin `platform_*` writes are separate (see
[Platform](#platform-super-admin)).

**Scope, and the rule that fixes it.** The write surface is exactly what these
REST routes expose, tool for tool: `api/routers/match_actions.py`,
`api/routers/tournament_actions.py`, `api/routers/check_in.py`,
`api/routers/memberships.py`, `api/routers/stage_actions.py`, the
position/shift/assignment writes and `POST /volunteers/assignments/{id}/check-in`
in `api/routers/volunteers.py`, the role and membership writes in
`api/routers/users.py`, the asset and lending writes in `api/routers/equipment.py`,
`api/routers/payouts.py`, `api/routers/notification_settings.py`, and `PATCH /discord-events/tournaments/{id}`.
The event-day set was added for SG Live 2026, where the desk, coordinators and
staff run the floor from their phones; the admin set followed so staff can set
up and adjust tournaments, the schedule and the floor from a connected client.
That is worth stating
because the alternative — adding whichever writes seem useful — leaves two
surfaces that drift and no answer for the next proposal. A write that belongs
here belongs in the REST router too, and the reverse. A tool may be friendlier
than its route where JSON-RPC needs it: `update_tournament` names fields to
remove in `clear` (an MCP argument can't tell "omitted" from `null`), and
`update_equipment` changes only what it is given where `PUT /equipment/{id}`
replaces the whole asset.

| Group | Tools |
|---|---|
| Scheduling | `create_match`, `submit_match_request`, `update_match`, `delete_match`, `extend_match_to_set` |
| Stream and stations | `set_match_stream_candidate`, `assign_match_stage`, `assign_match_stations` |
| Lifecycle | `seat_match`, `start_match`, `finish_match`, `confirm_match`, `record_match_result`, `set_match_review`, `generate_match_seed` |
| Crew approval | `set_crew_approval` (staff, tournament admins, crew coordinators) |
| Your own participation | `signup_as_crew`, `withdraw_crew_signup`, `acknowledge_match`, `watch_match`, `unwatch_match`, `offer_match_for_stream`, `withdraw_stream_offer`, `sign_up_for_tournament`, `withdraw_from_tournament` |
| Check-in desk (`EVENT_CHECK_IN`) | `check_in_attendee`, `undo_attendee_check_in`, `link_attendee`, `unlink_attendee`, `add_walk_up`, `sync_check_in_event` |
| Volunteers (`VOLUNTEERS`) | `check_in_volunteer`, `call_off_volunteer`, `call_off_volunteer_shift`, `undo_volunteer_call_off` |
| Membership | `approve_join_request`, `deny_join_request` |
| Tournaments | `create_tournament`, `update_tournament`, `delete_tournament`, `add_tournament_admin`, `remove_tournament_admin`, `add_crew_coordinator`, `remove_crew_coordinator`, `enroll_tournament_player`, `remove_tournament_player`, `drop_tournament_player`, `update_tournament_discord_events`, `set_stage_best_of` (paired read: `get_round_schedule`) |
| Challonge (`CHALLONGE`) | `link_challonge_tournament`, `unlink_challonge_tournament`, `sync_challonge_tournament` |
| Stages | `create_stage`, `update_stage`, `delete_stage` |
| Volunteer schedule (`VOLUNTEERS`) | `create_volunteer_position`, `update_volunteer_position`, `delete_volunteer_position`, `create_volunteer_shift`, `update_volunteer_shift`, `delete_volunteer_shift`, `assign_volunteer`, `unassign_volunteer` |
| Members and roles | `add_community_member`, `remove_community_member`, `grant_community_role`, `revoke_community_role` |
| Equipment (`EQUIPMENT`) | `create_equipment`, `update_equipment`, `delete_equipment`, `check_out_equipment`, `check_in_equipment` |
| Payouts (`PAYOUTS`) | `set_tournament_prize_pool`, `set_tournament_payout_split` |
| Notification settings | `set_notification_setting`, `set_staff_notification_setting`, `reset_notification_settings` (staff, checked in `NotificationSettingsService`); `set_my_notification_preset`, `set_my_notification` (any member, own settings only) |

Every one is registered at **ACTOR**, mirroring `require_write_actor`: holding a
live token approved for writing is the bar at this layer, and the real check is
the service's own (`can_crud_match`, `can_run_match`, `can_confirm_match`,
`can_assign_match_stream`, `can_edit_tournament`, `can_manage_volunteers`,
`can_manage_equipment` and so on). The service check is the one that knows about
tournament admins, so duplicating it here would only give the two surfaces a way
to disagree. A writing grant is **not a promotion** — the token still acts as its
user.

**The membership floor.** `mcpserver/auth.py` refuses anyone who doesn't belong
to the community, and the REST API has no equivalent (a PAT is bound to one
community, so it never needed one). The floor is membership, not a role: it used
to ask for a role, which left a plain player with an empty `list_tenants` and
the seven self-service tools (crew signup and withdrawal, acknowledge, watch and
unwatch, tournament signup and withdrawal) out of reach, though the web and a PAT
gave them the same reads and actions. Members get exactly what a member's PAT
gets; `STAFF`/`ADMIN` gates still apply on top.

Every write that removes something rather than adding or amending it (the
`delete_*`, `remove_*`, `withdraw_*`, `unassign_volunteer`, `revoke_community_role`,
`undo_attendee_check_in`, `unlink_attendee`, `unlink_challonge_tournament` and
`reset_notification_settings`) is
additionally annotated `destructiveHint`, which is how a client decides how hard to
ask before proceeding. `tests/mcp/test_mcp_catalogue.py` holds the exact list.
`delete_tournament` also needs `confirmation="permanently delete"` and an inactive
tournament, the same two gates as the dialog.

### Hiding writes from a read-only listing

`WizzrobeMCP.list_tools` (in `mcpserver/server.py`) filters the write tools out
for a read-only connection. It is **not** the security boundary — the gate is,
and `test_mcp_writes.py` calls every write tool over the wire with a read-only
token to prove it. It is a context boundary: a read-only client offered twenty-one
tools it can never call spends tokens on their schemas and plans work it cannot
do, with the refusal arriving a round-trip after the model has told the user it
would reschedule their match.

The listing runs inside the request's context (see `mcpserver/asgi.py`), so the
actor is the one whose token is being served. Outside a request — the SDK builds
the list once while wiring itself up — `optional_actor()` returns `None` and the
full catalogue is returned.

## Response shapes

Two rules, in `mcpserver/schemas.py`:

- **Singular reads reuse the REST model.** `get_match` goes through
  `api/_match_view.serialize_match`, so rules baked into it cannot drift between
  the two surfaces. Writes follow the same rule and return the same
  `MatchResponse`, so the model reads back exactly what it changed; the ones
  with no match record to hand back (`delete_match`, the crew/acknowledge/watch
  self-actions, and the two tournament signup tools) return `OperationResult`.
- **List reads get a compact shape.** A hundred full match records is mostly
  padding, which buries the answer and burns the model's context.

A third rule is about the *annotation*, and it fails silently: **never return a
bare `dict`**. `func_metadata` derives the output schema from the return type,
and `dict` yields **no schema at all** — the SDK then sends the payload as a JSON
string in a text block instead of `structuredContent`, while a `Dict[str, Any]`
tool right beside it returns a properly typed result. Nothing in the tool's
source shows the difference, so
`test_mcp_catalogue.py::test_every_tool_declares_an_output_schema` asserts it.

Where a service payload is built for a chart rather than a reader, the tool
reshapes it — see the reports section above. `mcpserver/tools/_args.py` holds the
shared timestamp/limit parsing so every tool refuses a bad window in the same
words.

All datetimes are UTC as stored. Each community chooses the zone its members read them in; the model is told once in
the server instructions rather than per field.

Discord ids are strings (`mcpserver/snowflake.py`'s `Snowflake` type; the REST API still sends numbers): a
snowflake is past 2**53, so a JSON number is rounded by any JavaScript client.

## Errors

`Tool.run` in the SDK catches every exception and re-raises a bare `ToolError`,
so the type is gone above the tool. Mapping therefore happens inside the
per-tool wrapper (`mcpserver/errors.py`), not at a central seam. Ordering mirrors
`ServiceErrorRoute`, including `NotFoundError` before `ValueError` (it subclasses
it):

| Exception | Tool error |
|---|---|
| `PermissionError` | `forbidden: …` |
| `NotFoundError` (incl. `FeatureDisabledError`) | `not_found: …` |
| `ValueError` | `invalid_request: …` |
| anything else | `internal_error: …` (logged with traceback → Sentry; opaque to the model) |

The prefixes are machine-readable on purpose: they let the model tell "I lack
permission, tell the user" from "wrong id, list first" without parsing English.

Transport failures are HTTP-level and OAuth-shaped: **401** missing/invalid/
non-OAuth token, **403** deactivated account, **405** wrong method, **406** bad
`Accept`, **429** rate limited.

## Implementation notes

Four settings in `mcpserver/server.py` are load-bearing and non-obvious. Each is
pinned by a test.

- **`stateless_http=True` is a correctness requirement, not a scaling knob.** In
  stateful mode the MCP server task is spawned once per *session* and snapshots
  the context of whichever request created it, so a second token on that session
  would execute as the first user. Stateless spawns a task per HTTP request,
  which inherits that request's actor.
- **`json_response=True`** — the app's `BaseHTTPMiddleware` stack wraps `/mcp`,
  and `BaseHTTPMiddleware` around long-lived SSE is a known source of hangs on
  client disconnect.
- **`streamable_http_path='/'`** — the path is served from
  `mcpserver/__init__.py`; the SDK default would nest the real endpoint at
  `/mcp/mcp`.
- **DNS-rebinding protection is off explicitly.** It auto-enables when the
  configured host is loopback (the default) and would 421 every production
  request — failing in exactly the environment local testing cannot reveal.

Two more, elsewhere:

- **A `Route`, not a `Mount`.** Starlette compiles a mount to
  `^/mcp/(?P<path>.*)$`, so bare `POST /mcp` — what every client sends — would
  only reach it via a 307, and clients do not follow redirects by default. This
  is why `streamable_http_path` is `'/'`.
- **`mcpserver.mount(app)` must run before `frontend.init(app)`** in `main.py`.
  NiceGUI mounts itself at `/` and would swallow `/mcp`.

`MCPServer.__init__` calls `logging.basicConfig`, so the server is built inside
`mount()` rather than at import time — otherwise it wins the race against
`main.py`'s own logging setup and strips the format process-wide.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `MCP_ENABLED` | on | Kill switch. When off there is no `/mcp` route, no discovery routes, and the lifespan hook is a no-op. |
| `BASE_URL` | `http://localhost:8000` | The resource identifier (`{BASE_URL}/mcp`) and the OAuth issuer. Must be the public URL. |
| `API_RATE_LIMIT_PER_MIN` | 120 | Shared with `/api`. |

A reverse proxy must pass `/mcp` and `/.well-known/*` through unrewritten.
`FASTMCP_*` environment variables are inert — constructor arguments outrank them
in pydantic-settings.

## Testing

- `tests/mcp/` — transport, catalogue guardrails, the authorization matrix, the
  full OAuth flow, and `test_mcp_reads.py`, which calls every read tool against
  seeded rows (the catalogue tests prove a tool is *served* and *gated* but never
  run it, so a compact shape reading a renamed relation or an un-prefetched FK
  would ship green). The harness is `tests/mcp/conftest.py::mcp_session`, an
  async context manager used **inside** the test body: the session manager is
  single-use per instance and is an anyio cancel scope, which must be exited in
  the task that entered it, so a `yield`-style fixture cannot work.
- `tests/mcp/test_mcp_writes.py` — the write surface in three layers: the
  connection gate (every write tool called over the wire with a read-only
  token), the role gate underneath it, and whether the writes actually land.
  Plus the consent decision itself — the box unticked mints a read-only token, a
  client that requests write without it still gets one, and a refresh cannot
  widen the grant.
- `tests/tenancy/test_mcp_tenant_isolation.py` — scoping plus context hygiene.
  Its interleaving test catches what a single-call test cannot: a binding set
  without a matching reset passes every one-call-at-a-time test.

Dev fixtures: `scripts/seed_dev.py` seeds a registered client and **two**
deterministic OAuth bearers so `/mcp` can be driven with curl without the browser
flow — `wizzrobe_mcp_devseed_local_only_do_not_use` (read-only) and
`wizzrobe_mcp_devseedwrite_local_only_do_not_use` (writing). Two, because the
surface a token is served depends on the consent decision behind it; one would
leave half the server unreachable from a dev loop.
