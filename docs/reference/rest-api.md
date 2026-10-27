# REST API Reference

_Reference for Wizzrobe's REST API: the router package in [`api/`](../../api/) and the FastAPI app metadata in [`main.py`](../../main.py). Part of the [documentation index](../README.md)._

## Overview

| | |
|---|---|
| Title | Wizzrobe API |
| Version | 1.0.0 |
| Base path | `/api` |
| Swagger UI | `/api/docs` |
| ReDoc | `/api/redoc` |
| Source | [`api/`](../../api/) package — `routers/`, `schemas/`, `dependencies.py`, `rate_limit.py`; mounted in [`main.py`](../../main.py) |

The API is served by the same Uvicorn process as the NiceGUI frontend. The interactive Swagger UI at `/api/docs` is the authoritative, always-current catalogue (every endpoint, schema, and example); this page summarises the model.

### Documenting an endpoint

`/api/docs` is public and is the only documentation an API user sees, so it carries what a caller needs and nothing internal. The document is built by [`api/openapi_docs.py`](../../api/openapi_docs.py), which splits the work in two:

- **Written by hand on each route:** `summary=` (a few words) and `description=`, which says what the call does, any side effect a caller should know about (who gets a Discord DM, what gets cleared), the specific reasons it answers `400`, and ends with a paragraph `**Who can call this:** <who>.` Check "who" against both the route's dependency and the service checks behind it. Always pass `description=`: without it FastAPI publishes the handler's docstring, which is written for developers. Request and response fields get `Field(description=...)`; say times are UTC and list enum values. A route that returns only `{"detail": "..."}` uses `response_model=DetailResponse` ([`api/schemas/detail.py`](../../api/schemas/detail.py)).
- **Derived from the route's dependencies, never written by hand:** the token note (no token needed, or read-only tokens refused), the feature-flag note (from `require_feature`, which tags its dependency with the flag), the shared `400`/`401`/`403`/`404`/`429` responses with the `ErrorResponse` body, section names and order (`TAG_GROUPS`, plus `TAG_RENAMES` for older router tags), and the **Webhooks** section, one entry per event family built from `EventType.ALL` and `WebhookService.format_reference()`.

The intro, including the getting-started recipes, is `api_description()` in the same module. [`tests/api/test_openapi_docs.py`](../../tests/api/test_openapi_docs.py) fails when a route has no `**Who can call this:**` line, a JSON response has no schema, or a router tag isn't in `TAG_GROUPS`.

## Authentication

**Every endpoint requires a personal bearer token**, with three exceptions: the unauthenticated `GET /api/health` liveness probe (see [Health](#health-apihealth--healthpy)), `GET /api/version` (see [Version](#version-apiversion--versionpy)), and `POST /api/web-push/rotate` (see [Web push](#web-push-apiweb-push--web_pushpy)), whose only caller is a service worker with no session to present.

Generate a token on the home **Profile** tab (the *API tokens & AI clients* card), then send it on each request:

```
Authorization: Bearer wizzrobe_pat_xxxxxxxx...
```

- A token is **bound to exactly one tenant**, chosen at creation. `resolve_token` reads `token.tenant_id`, returns `403` if that tenant is missing or inactive or the token's owner is no longer a member of it (a super-admin excepted), and sets the request tenant context — the API is excluded from `TenantMiddleware`, so the **token, not the URL, is what scopes the request**. A router-level `tenant_context_scope` dependency baselines the contextvar to `None` and resets it after the response, so a tenant never leaks to the next request on a reused task.
- A token **acts as its owning user** and inherits that user's exact permissions and scope *within that tenant* — the same `AuthService` role checks that gate the web UI apply. A non-staff token gets `403` from staff-only routes; a Tournament Admin token can edit only its own tournaments; a self-action (acknowledge, sign up, edit own profile) only ever affects the token's user.
- A token may be flagged **read-only**, in which case it can call read (`GET`) endpoints only; any write returns `403`.
- Tokens are stored as a SHA-256 hash (the plaintext is shown once at creation), support an optional expiry, and can be revoked at any time. Removing someone from a community revokes every token they hold for it (`TenantMembershipService.remove_member`), since a token is bound to one community and carries no role of its own. Implementation: [`ApiToken`](../../models/user.py) model, [`ApiTokenService`](../../application/services/api_token_service.py), auth dependency in [`api/dependencies.py`](../../api/dependencies.py).

Tokens can also be managed programmatically via the `/api/tokens` endpoints (creating/revoking a token requires a non-read-only token, so a read-only token can never mint a more privileged one, and a token minted over the API never expires later than the token that minted it).

**MCP tokens are not REST credentials.** `ApiToken` also stores the platform-wide OAuth tokens issued to MCP clients (`origin='oauth'`, `tenant=NULL`); those are refused here with `401`, and a personal access token is refused at `/mcp` with `401` plus the OAuth challenge. A credential minted for one surface can never be replayed against the other. The MCP server is a sibling entry surface, not part of this API — see [features/mcp-server.md](../features/mcp-server.md).

### App scopes

Third-party apps signed in through OAuth call this same API with tokens of `origin='app'`. Such a token carries the scopes its user approved, space-separated in `ApiToken.scope`, and `resolve_token` refuses any route outside them with `403` and `WWW-Authenticate: Bearer error="insufficient_scope", scope="<needed>"`.

- **Personal access tokens are unscoped** and skip the check entirely; a read-only PAT is still limited by `require_write_actor` as before.
- **The vocabulary** (eight area scopes: `profile`, `profile:write`, `schedule:read`, `play`, `crew`, `volunteer`, `staff:read`, `staff:write`, with their consent wording) lives in [`application/api_scopes.py`](../../application/api_scopes.py), shared with the authorization server.
- **The route table** in [`api/scopes.py`](../../api/scopes.py) lists every route exactly once: under the scope an app needs, under `PAT_ONLY` (tokens, webhooks, config, Discord role mappings, service health, racetime bots, `/platform`), or under `PUBLIC` (the two tokenless routes). A route missing from the table is refused for apps. `tests/api/test_scopes.py` fails when a route is unmapped, when a staff-gated route sits under a non-staff scope, when a super-admin route isn't `PAT_ONLY`, or when a read lands on a write-only scope (or the reverse). **Adding an endpoint means adding a line there.**
- **Scopes only narrow.** The service's own role checks still run, so `staff:read` on a player's grant reaches nothing.
- `/api/docs` shows each endpoint's scope (or "Apps: can't call this") and declares a `WizzrobeApps` OAuth 2 security scheme with the scope list.

### Status codes

| Code | Meaning |
|---|---|
| `401` | Missing, malformed, unknown, revoked, or expired token |
| `403` | Inactive account or tenant, read-only token used for a write, an app token without the route's scope, or the user lacks the required role (`PermissionError` from a service) |
| `400` | Validation/business-rule error (`ValueError` from a service) |
| `404` | Entity not found (`NotFoundError` from a service, including `FeatureDisabledError`), or a feature-flag-gated router the tenant hasn't enabled |
| `422` | Request body/query failed schema validation |
| `429` | Rate limit exceeded — carries a `Retry-After` header |
| `503` | `GET /health` only: the database is unreachable |

`PermissionError` → `403`, `NotFoundError` → `404` (checked first, since it subclasses `ValueError`) and `ValueError` → `400` translation is handled by `ServiceErrorRoute` in [`api/dependencies.py`](../../api/dependencies.py), scoped to the API routers so the NiceGUI frontend is unaffected.

### Rate limiting

`rate_limit` ([`api/rate_limit.py`](../../api/rate_limit.py)) is a dependency on the aggregating router, so it applies to **every** endpoint including `/health`. It is an in-process fixed-window counter (the app runs single-worker), `API_RATE_LIMIT_PER_MIN` requests per minute, default 120.

It runs **before** authentication, so unauthenticated floods are capped too. The client IP is always enforced as a ceiling; a *well-formed* bearer token additionally gets its own bucket (keyed by SHA-256 hash, never the raw secret) so one client's traffic is throttled independently below that ceiling. Garbage or rotating bearer values fall through to the IP key — the `wizzrobe_pat_` prefix is public, so bucketing solely on a presented token would let an attacker mint a fresh empty bucket per request. `X-Forwarded-For` is trusted only when `TRUST_PROXY_FORWARDED_FOR` is set, since the header is client-spoofable.

A third-party app's calls also count against one bucket for the whole app (`API_APP_RATE_LIMIT_PER_MIN`, default 600), checked once its grant is resolved. A refusal there says so (`This app is making too many requests`) with `Retry-After`. See [OAuth apps › Limits](../features/oauth-apps.md#limits-and-browser-access), which also covers CORS for browser apps.

### Events

A token is bound to one community; within it, a call works in one **event**
([events.md](../features/events.md)).

- **Event-owned routers** (matches, tournaments, brackets, stages, volunteers,
  check-in, qualifiers, race rooms, reports, seeds, triforce texts, SpeedGaming,
  crew, payouts, reschedule requests, Discord events) attach
  `Depends(event_binding(**rows))` from `api/dependencies.py`. It reads an
  optional `?event=<slug>`; leaving it out keeps the default event. `rows` maps
  each path parameter that names a row to its model (`match_id=Match`): such a
  call works in the row's own event, and an `event` that names another one is
  `404`, the same wall as another community's row. A draft is `404` for anyone
  `EventService.can_view` refuses. `tests/api/test_event_binding.py` fails when
  an event-owned router has no binding, or a path parameter is neither mapped
  nor listed in its `NOT_ROWS`.
- **A write in an archived event is `409`** (`EventArchivedError`).
- **Community-wide reports** (audit, sent messages, telemetry) take
  `Depends(event_filter)` instead: `?event=` narrows the rows to that event and
  binds nothing; left out, every row comes back, including ones that belong to
  no event.
- **Exports** bind the event on `POST /exports/tournaments` and
  `/exports/volunteers`; the worker builds the export in the event it was asked
  for in, archived ones included.

## Architecture

API handlers are a thin presentation layer: they authenticate, then delegate to the **service layer**, passing the token's user as `actor`. Services enforce permissions, validate, write audit logs, and queue Discord notifications — so the API automatically inherits all of that behaviour. Handlers contain no business logic and (aside from simple read lookups) no direct ORM writes.

## Endpoint catalogue

Grouped by domain (tag). See `/api/docs` for parameters, request/response schemas, and examples.

One module per group under [`api/routers/`](../../api/routers/), named after the group, with a matching module under [`api/schemas/`](../../api/schemas/). The cross-cutting pieces are `dependencies.py` (auth deps + `tenant_context_scope` + `require_feature` + `ServiceErrorRoute`), `rate_limit.py`, `_helpers.py` (shared load-or-404), and `_match_view.py` (match serialization).

### Health (`/api/health`) · `health.py`
- `GET /health` — **unauthenticated** liveness probe. Performs a trivial DB round-trip and returns `{"status": "ok"}`; returns `503` when the database is unreachable. Used by the container `HEALTHCHECK`.

### Version (`/api/version`) · `version.py`
- `GET /version` — **unauthenticated**. Which build is answering: `version` (the tag, else the commit's UTC date as `YYYY.MM.DD`, else `dev`), `commit`, `commit_short`, `commit_date` and `source_url`, from `application/utils/build_info.py`. The same values are printed on `/about` and returned by the MCP `server_version` tool.

### Web push (`/api/web-push`) · `web_push.py`
- `POST /web-push/rotate` — **unauthenticated**. Called by `static/sw.js` on `pushsubscriptionchange` to re-point a stored device row at the subscription its push service reissued. There is no session or token in that context; the request authenticates by carrying the retired subscription's `auth` secret, and a wrong secret returns the same `404` as an unknown endpoint. Detail: [features/web-push.md](../features/web-push.md#authenticating-a-rotation).

### Matches (`/api/matches`) · `matches.py`, `match_actions.py`
- `GET /matches` — list with filters (`match_id`, `stage_id`, `tournament_id`, `start_date`, `end_date`, `limit`); only approved crew are exposed.
- `GET /matches/{id}` — single match. Alongside the lifecycle timestamps the response carries `needs_review`, the proctor's dispute flag. It is a bare flag: there is no note field, and confirming clears it.
- `POST /matches` — create (Staff/TA). `POST /matches/request` — player-initiated request.
- `PATCH /matches/{id}` · `DELETE /matches/{id}`.
- `POST /matches/{id}/extend-to-set` (`{later_games: [{scheduled_date, scheduled_time}], allow_outside_round?}`) turns a Challonge or start.gg booking made as one game into game 1 of a best-of set and books games 2 and up with the same players. The match keeps its crew, seed, stage and comments. `400` once it's in a set, started, unlinked from a bracket, or a time is out of order or outside the stage's window. Staff or the tournament's admins.
- `POST /matches/{id}/stream-candidate` · `/stage` · `/stations`.
- `POST /matches/{id}/stations/suggest` — draw a random seating (opposite sides, spread out) without applying it. Returns `{assignments, relaxations}`. Takes the **write** actor even though it writes nothing: it is a preview of `POST /stations`, and a read-only token has no business drawing seats it cannot then take.
- Lifecycle: `POST /matches/{id}/seat` · `/start` · `/finish` · `/confirm` · `/result` · `/seed`. `/confirm` returns `400` unless the match is finished **and** a winner has been recorded.
- `POST /matches/{id}/review` — `{needs_review}`. Raises or drops the dispute flag on a recorded result, returning the updated match. The two directions gate differently: flagging is `can_run_match` (the proctor's own call, and `400` unless the match is finished), clearing is `can_confirm_match` (`403` for a proctor). `/confirm` clears the flag on its own — see [match-participation.md](../features/match-participation.md#disputed-results).
- Self-actions: `POST /matches/{id}/crew` (sign up) · `DELETE /matches/{id}/crew/{role}` · `POST /matches/{id}/acknowledge`.
- Watching: `GET /matches/watching` (matches you watch) · `POST /matches/{id}/watch` · `DELETE /matches/{id}/watch`.
- Stream offer: `POST /matches/{id}/stream-offer` · `DELETE /matches/{id}/stream-offer` — a player offers their own match for stream coverage, or takes it back. `400` for someone not in the match, or for an offer once it has started (withdrawing is allowed after). Stream managers see the offer; it does not set `is_stream_candidate`.

- `GET /matches/{id}/crew` — the coordinator's crew list, **including unapproved signups** (staff, or a TA/CC of the match's tournament: `can_approve_crew`, checked in `CrewService.get_match_crew`). `GET /matches/{id}` hides those from everyone, so this sits behind a higher gate than the match itself; the peer of the MCP `list_match_crew` tool.

### Reschedule requests · `reschedule_requests.py`
Players asking staff to move or call off a match. Approving **performs** the change, so a `POST /approve` is a reschedule or a cancellation, not a status update.
- `POST /matches/{id}/reschedule-requests` — raise one (`{kind, reason, proposed_at?}`). `403` for a non-player or a tournament with requests turned off; `400` for a match under way, a SpeedGaming-sourced match, a past proposal, or a second open request by the same player.
- `GET /reschedule-requests` — the queue. Staff see all of it; a tournament admin sees their own tournaments' requests; everyone else gets `403`. A request carries the player's own words about why they need the change, which is not schedule data and is nobody else's to read.
- `GET /matches/{id}/reschedule-requests` — whoever could decide these, or a player in that match reading their own; `403` otherwise.
- `GET /reschedule-requests/mine` — your own, any token.
- `POST /reschedule-requests/{id}/agree` — the opponent's advisory agreement. `403` for the requester themselves or an outsider.
- `POST /reschedule-requests/{id}/withdraw` — the requester's alone.
- `POST /reschedule-requests/{id}/approve` — `{scheduled_at?, note?}`. Omitting `scheduled_at` takes the proposed time; supplying one counters with a different time. `400` when the request named no time and none is given.
- `POST /reschedule-requests/{id}/decline` — `{note}`. The note is **required** (`422` without it).

Raising, agreeing and withdrawing take any write token (the service checks the actor plays in the match); deciding is re-gated in the service on `can_crud_match`, which is wider than global STAFF. Every gate lives in the service rather than the router, so the web, Discord and MCP surfaces inherit it. All timestamps are UTC, like the rest of the API — only Discord renders per-viewer.

### Crew (`/api/crew`) · `crew.py`
- `POST /crew/{crew_type}/{crew_id}/approval` — approve/reject (Staff/TA/Crew Coordinator).
- `POST /crew/{crew_type}/{crew_id}/acknowledge` — crew member acknowledges their own approved assignment.

### Tournaments (`/api/tournaments`) · `tournaments.py`, `tournament_actions.py`
- `GET /tournaments?active_only=` (`active_only` returns only active tournaments, default `false`) · `GET /tournaments/{id}`. Inactive tournaments are hidden from anyone without `can_view_admin` (listed out, **404** by id), as in the app.
- `POST` · `PATCH /{id}` · `DELETE /{id}?confirmation=permanently%20delete`. The delete is permanent and cascades (matches, entrants, brackets, triforce texts), so it carries the same two gates as the UI: the tournament must already be `is_active: false` (`400` otherwise) and `confirmation` must be `permanently delete` (`422` when the param is missing, `400` when it is wrong). `POST`/`PATCH` accept an optional per-tournament **tournament-days** override — `event_start_date`, `event_end_date` (`YYYY-MM-DD`), and `tournament_hours` (`{"YYYY-MM-DD": ["HH:MM open", "HH:MM close"]}`); each is nullable and falls back to the community setting when omitted (on `PATCH`, sending `null` clears an override back to inherit). An end before start, or a close not after open, returns `400`.
- `POST /{id}/admins` · `DELETE /{id}/admins/{user_id}` and `POST /{id}/crew-coordinators` · `DELETE /{id}/crew-coordinators/{user_id}` (Staff).
- `POST`/`PATCH` also take the configuration the edit dialog sets: `is_test`, `allow_player_match_requests`, `stage_reminder_minutes`, `preset_id`, `hard_preset_id`, and the racetime knobs (`racetime_bot_id`, `race_room_profile_id`, `racetime_auto_create_rooms`, `room_open_minutes_before`, `require_racetime_link`, `racetime_default_goal`). On `PATCH`, `null` clears the nullable ones (a preset, the bot, the profile, the goal). Every tournament response carries them, plus `challonge_tournament_url` and `discord_events_enabled`.
- `GET /{id}/players` (Staff or that tournament's admin) · `POST /{id}/players` `{user_id}` · `DELETE /{id}/players/{user_id}` (Staff or that tournament's admin) — the roster, as Manage players edits it; enrolling ignores the signup window and refuses a non-member.
- `PUT /{id}/challonge` `{challonge}` (URL or ID) · `DELETE /{id}/challonge` · `POST /{id}/challonge/sync` (Staff; `404` unless the community has `CHALLONGE`) — link, unlink and sync a Challonge bracket, the same as the Tournaments row ⋮.
- `GET /{id}/round-schedule` · `PUT /{id}/round-schedule/{stage_key}/best-of` `{best_of}` (1, 3 or 5) (Staff or tournament admin; `400` for a tournament with no Challonge or start.gg bracket, or an unknown stage) — the stages Round schedule shows, with window, best-of and booked counts; set one stage's best-of without touching its window.
- `POST /tournaments/{id}/signup` · `DELETE /tournaments/{id}/signup` — the caller signs **themselves** up or withdraws, the same act the Tournaments tab performs. `400` outside the tournament's signup window, for a non-member, on a duplicate signup, and for an inactive tournament; for a Challonge-linked tournament also when the caller hasn't linked Challonge, the bracket has started, or the connection lacks `participants:write` (a successful call adds them to the Challonge bracket); `404` for an id that does not exist in this tenant (saying "not accepting signups" there would confirm a tournament that isn't yours). Withdrawing is refused only once the window has **closed** — before it opens you may still back out, since staff may have entered you early and there is no roster to protect yet. Staff enrolling *someone else* use `PUT /users/{user_id}/tournaments`, which ignores the window.
- The signup window itself rides on the tournament: `signups_open_at` / `signups_close_at` (UTC datetimes) on `POST`/`PATCH` and in every tournament response. Both are nullable and read permissively — no open date means signups are open now, no close date means they stay open. A close at or before the open returns `400`.
- `GET /tournaments/{id}/bracket?phase=&pool=&state=&entrant=&include_sets=` — the whole bracket (native, start.gg or Challonge) in one shape, the same payload as MCP `get_tournament_bracket`: phases, pools, sets with feeders and where the winner and loser go, scores, booked matches and standings. Read from Wizzrobe's synced mirror, never a live call to the site. Entrants' `user_id` only for Staff and the tournament's admins. `400` for a bad `state`; `404` for an inactive tournament (non-admins), a native one with nothing published (non-Staff), or when the bracket site's feature is off.
- `GET /tournaments/{id}/match-suggestion?player_ids=[&bracket_match_id=]` — suggested UTC start time for the given players (400 if no slot fits). `bracket_match_id` confines the suggestion to that matchup's round window.

### Payouts (`/api/tournaments/{id}/payouts`) · `payouts.py`
Behind `FeatureFlag.PAYOUTS` (the whole router 404s when the community lacks it). Staff or that tournament's admin; **reads are gated with the writes**, since an unannounced split is admin data. Money serialises as strings, never floats. See [payouts.md](../features/payouts.md).
- `GET /tournaments/{id}/payouts` — the split with each line's `amount` computed from `prize_pool + prize_bonus` at read time.
- `PUT /tournaments/{id}/payouts` — replace the whole split with `{"lines": [{"place", "percentage", "entrant_id"?, "note"?}]}`. Two lines may share a `place`. `400` when the shares sum above 100; below 100 is accepted, because an unallocated remainder is real.
- `PUT /tournaments/{id}/prize-pool` — `{"prize_pool", "prize_bonus"}`, both optional and nullable. Omitting one clears it, which is not the same as sending `0`.

### Stages (`/api/stages`) · `stages.py`, `stage_actions.py`
- `GET /stages?active_only=` (`active_only` returns only active stages, default `false`) · `GET /stages/{id}` · `POST` · `PATCH /{id}` · `DELETE /{id}` (Staff or Stream Manager).

### Users & roles (`/api/users`) · `users.py`
- `GET /users?role=` (Staff) returns the **members of the token's community**, not every account on the platform — identity is global but this list is not. `role` filters to users holding that role in the token's tenant. **Behaviour change:** it previously returned every `User` row on the deployment; there is deliberately no `?scope=all`, which would re-open the leak behind a parameter. A user appears once they are a member — granted a role, added from the Users tab, or created there. · `GET /users/me` · `GET /users/{id}` (self or Staff, **and only within the token's community** — see below).
- `POST /users` (Staff; always creates an active account, and a `discord_id` that already has one is `400`) · `PATCH /users/me` · `PATCH /users/{id}` · `PATCH /users/{id}/admin` (Staff) · `PUT /users/{id}/tournaments` (Staff for anyone; a player editing their own list goes through the same signup rules as `POST /tournaments/{id}/signup`).
- `POST /users/{id}/roles` · `DELETE /users/{id}/roles/{role}` (Staff) — grant/revoke a role. Roles are per-tenant except `SUPER_ADMIN`; see [authentication.md § Roles](authentication.md#roles).
- `POST /users/{id}/membership` · `DELETE /users/{id}/membership` (Staff) — add someone to this community or take them out, as the Users tab does. Removing is `400` while they hold a role here or admin or coordinate a tournament.

### Player availability (`/api/users/me/availability`) · `player_availability.py`
- `GET` (own windows) · `PUT` (replace all windows) · `DELETE` (clear). Self-service for any authenticated user; windows feed match-time suggestions. **Opt-out:** a player with no windows is available for the whole event, so a window is a time they cannot play (`unavailable`) or would rather (`preferred`) — an `available` window is accepted but states the default. Volunteer availability, on the same wire shape, is the opposite (opt-in).
- **Which by-id routes are community-scoped.** `User` is global, so a bare id lookup reaches every account on the platform — the same leak `GET /users` was narrowed for, behind a loop instead of a parameter. The routes that *read or edit the person* (`GET /users/{id}`, `PATCH /users/{id}`, `PATCH /users/{id}/admin`, `PUT /users/{id}/tournaments`, `GET /users/{id}/availability`) resolve only members of the token's community, via `load_community_user_or_404`. `PATCH /users/{id}/admin` matters most: `is_active` is a *global* account disable, so cross-community reach there locks someone out of communities the actor has nothing to do with. A non-member is `404`, never `403` — "no such id" and "not in your community" must read the same, or the status code is a membership oracle. You always resolve yourself, and a super-admin resolves anyone.
- **Global account fields need more than membership.** Display name, pronouns and `is_active` belong to the account, not the community, and staff can make anyone a member by granting a role. So `PATCH /users/{id}` and `PATCH /users/{id}/admin` refuse (`403`) when the person also belongs to another community or is a super-admin; only a super-admin changes those. Staff remove such a person from their own community instead.
- **Ids in a request body are community-scoped too.** Match rosters (`player_ids`, `commentator_ids`, `tracker_ids` on create, update and player match requests), volunteer shift assignments and bracket entrant links refuse anyone who isn't a member of the token's community, with the same `404` an unknown id gets. On an update only the people being added are checked. A super-admin may name anyone. Equipment loans are the one deliberate exception: lending to someone who just walked in is what that flow is for.
- **Which stay global, deliberately.** The routes that *confer* something — `POST/DELETE /users/{id}/roles`, and the tournament-admin, crew-coordinator and qualifier-admin membership routes — keep the unscoped `load_user_or_404`, because granting is how a person joins a community in the first place (`grant_role` calls `ensure_member` as it goes). Nothing there discloses the person: the caller already had to know the id, and the response says only what was granted.
- `GET /users/{id}/details` (**Staff**, members of the token's community only, else `404`) — the data behind Admin → Members' member sheet: roles, tournament admin/crew coordinator/entered tournaments, upcoming matches and shifts, equipment out, check-in state, linked accounts, how DMs reach them (`channels`: Discord on/off, push device count, email and Telegram as states, never the address), their preset, every `notifications` row with `community`, `theirs`, `effective` and a `reason` when they differ, and their last 10 `recent_messages` (the Sent messages entry shape). Emergency contacts are not included. `MemberDetailsService.get`; MCP twin `get_member_details`.
- `GET /users/{id}/availability?start=&end=` — another player's windows (self, or Staff for anyone else), optionally bounded to a UTC window. The scheduler's read, matching `GET /users/{id}` and the MCP `get_player_availability` tool. It rides a second router so the literal `/users/me/…` paths keep matching before the `{user_id}` param.

### Volunteers (`/api/volunteers`) · `volunteers.py`
Positions, shifts, and assignments for volunteer scheduling; the `/me/*` routes are self-service. All routes require an authenticated actor; writes use a non-read-only token. The roster reads (`GET` positions, shifts, a shift, coverage) need `AuthService.can_view_volunteer_schedule` — the Volunteer hub's roles (Staff, Proctor, Volunteer) plus volunteer coordinators — and **403** anyone else.
- **Positions:** `GET /volunteers/positions?active_only=` (list; `active_only` limits to active positions, default `false`) · `POST /volunteers/positions` (create) · `PATCH /volunteers/positions/{id}` · `DELETE /volunteers/positions/{id}`.
- **Shifts:** `GET /volunteers/shifts?start=&end=` (list shifts in a UTC ISO-8601 window; `start`/`end` required) · `GET /volunteers/shifts/{id}` · `POST /volunteers/shifts` (create) · `PATCH /volunteers/shifts/{id}` (partial; moving the time re-asks acknowledgment and DMs the volunteers) · `DELETE /volunteers/shifts/{id}`.
- **Assignments:** `POST /volunteers/shifts/{shift_id}/assignments` (assign a volunteer; returns the assignment plus any warnings) · `DELETE /volunteers/assignments/{id}` (remove) · `POST /volunteers/assignments/{id}/acknowledge` (acknowledge your own assignment) · `POST /volunteers/assignments/{id}/check-in` (record that the volunteer arrived; coordinators and staff, `403` otherwise; a second call keeps the first time) · `POST` / `DELETE /volunteers/assignments/{id}/call-off` (call one volunteer off, optional `note`, or undo it; the shift still counts as worked) · `POST /volunteers/shifts/{shift_id}/call-off` (everyone on the shift; drafts left alone).
- **Coverage:** `GET /volunteers/coverage?start=&end=` — per-shift coverage across a UTC ISO-8601 window (`start`/`end` required).
- **Hours:** `GET /volunteers/hours?start=&end=` — every pool member's (VOLUNTEER or STAFF role) served hours against the comp tiers, coordinator-only (staff or volunteer coordinator; 403 otherwise) · `GET /volunteers/hours/me` — your own. `start`/`end` are optional **local dates** that narrow the count; both default to the tenant's event window. Published assignments only, overlapping shifts counted once.
- **Self-service (`/me`):** `GET /volunteers/me/availability` · `PUT /volunteers/me/availability` (replace windows) · `GET /volunteers/me/assignments?upcoming_only=` (your shift assignments; `upcoming_only` defaults to `true`, and unpublished drafts are never listed) · `DELETE /volunteers/me/assignments/{id}` (give a shift back, optional `{"reason": …}` body → 204; the coordinators are DMed).

### Triforce texts (`/api/triforce-texts`) · `triforce.py`
- `GET /mine?tournament_id=` (own; `tournament_id` required) · `GET ?tournament_id=&status=` (moderation, Staff/TA).
- `POST` (submit) · `POST /{id}/moderate` · `DELETE /{id}` (Staff/TA).

### Equipment (`/api/equipment`) · `equipment.py`
Lending inventory and the checkout workflow. **Reads take any token** — the gate of record is the home **Equipment** tab, which carries no role check, so every member of a community with the feature on sees the inventory and who holds each item. `private_notes` is the one field that isn't world-readable: it is returned only to a caller who passes `can_manage_equipment` (Staff or Equipment Manager) and is `null` for everyone else. Writes are re-gated in `EquipmentService`.
- **Reads:** `GET /equipment?status=` (`available` / `checked_out` / `retired`; the whole register, Staff or Equipment manager only, **403** otherwise) · `GET /equipment/{id}` (adds `current_loan`) · `GET /equipment/{id}/loans?limit=` (history, newest first) · `GET /equipment/me/checkouts` (what you are holding).
- **Assets:** `POST /equipment` · `POST /equipment/bulk` (`{name, count}`, 1–200 identical assets with consecutive numbers) · `PUT /equipment/{id}` · `DELETE /equipment/{id}` (Staff/Equipment Manager; `400` on an asset that is checked out).
  `PUT`, not `PATCH`, because the service takes the whole record: `description`, `private_notes` and `owner_user_id` are cleared when omitted. `status` is the exception (omit to leave it), and it cannot move into or out of `checked_out` — that is what the two lending routes are for.
- **Lending:** `POST /equipment/{id}/checkout` (optional `{borrower_id}` — only an Equipment Manager may name someone else, and a non-manager who sends one gets **403** rather than a loan against themselves; with no body, any authenticated member borrows to themselves) · `POST /equipment/{id}/checkin` (Staff/Equipment Manager only — the check-in gate is deliberately narrower than checkout, which is role-less).

### Surveys (`/api/surveys`) · `surveys.py`
Post-event surveys, Staff only, behind `FeatureFlag.SURVEYS` (see [surveys.md](../features/surveys.md)). Writes build a draft and nothing more: sending, reminding and closing stay buttons in Admin → Surveys, so no route DMs anyone. Question `kind` includes `agree_scale` (its `options` are the statements; results carry `statements`, per statement the count at each level). AI "Suggest questions" is web-only: no route calls the provider.
- **Reads:** `GET /surveys` (the event's surveys with sent/answered counts) · `GET /surveys/conditions` · `GET /surveys/{id}` (sections and questions, with their `conditions`) · `GET /surveys/{id}/results` (small groups hidden) · `GET /surveys/{id}/responses?limit=&offset=` (anonymous ones carry no user).
- **Writes:** `POST /surveys` · `POST /surveys/{id}/duplicate` · `PATCH /surveys/{id}` (`closes_at` is the only field an open survey takes) · `DELETE /surveys/{id}` (drafts only, `400` otherwise) · `POST /surveys/{id}/sections` · `PATCH`/`DELETE /surveys/sections/{section_id}` · `POST /surveys/sections/{id}/move` · `POST /surveys/sections/{id}/questions` · `PATCH`/`DELETE /surveys/questions/{question_id}` · `POST /surveys/questions/{id}/move`.
- `conditions` is a list of `{key, question_id?, values?}`, all of which must hold: up to 3 on a question, 1 on a section. `[]` clears them on a `PATCH`.

### Feedback (`/api/feedback`) · `feedback.py`
In-app feedback: submit, read your own, and the staff queue.
- `POST /feedback` (`{message, category?, page_url?}`) · `GET /feedback/mine?limit=` — any token; a person's own submissions carry their review status. Submitting needs membership of the community (or super-admin); a non-member gets `403`.
- `GET /feedback?limit=` — the queue (Staff), mirroring the Admin → Feedback tab.
- `POST /feedback/{id}/reviewed` (`{reviewed}`) — mark reviewed, or put it back in the queue. Reversible on purpose. The service gates this at `can_view_admin`, which is **wider than the queue's STAFF**: a tournament admin can clear a submission without being able to list them all.

### Reports (`/api/reports`) · `reports.py`
The programmatic peer of the Admin → Reports tab, at that tab's own gate (`can_view_admin`: staff, tournament admin, or crew coordinator). All reads, so a read-only token works throughout. A window is `start`/`end` (UTC ISO 8601, both required); `end` before `start` is `400`, and `bucket` accepts `week` or `month`.
- `GET /reports/capacity-forecast` · `/stage-utilization` · `/match-operations` · `/crew-coverage` · `/tournament-health` · `/crew-participation-trends` · `/activity-trends`.
- `GET /reports/matches-active-at?instant=&limit=` — what is in progress at one moment.
- `GET /reports/volunteer-hour-trends` — carries the `VOLUNTEERS` flag **at the route** rather than the router, because `AnalyticsService` has no flag of its own; without it a community with volunteers off could still read its volunteer hours. Its siblings on the same router stay reachable. It is also the one report a volunteer coordinator may read (`require_volunteer_admin`), so the router puts its gate on each route rather than on the router.

Two are **reshaped rather than passed through**, exactly as the matching MCP tools are: `ReportsService` builds its payloads for a chart, and `stage_utilization`'s even contains ORM `Match` rows. `capacity-forecast` returns peaks and headroom instead of the per-interval series; `stage-utilization` returns per-room totals plus `unplaced_candidate_count` instead of every booking.

### Telemetry (`/api/telemetry`) · `telemetry.py`
Engagement telemetry (Staff; `TelemetryService` re-checks that itself).
- `GET /telemetry/summary?start=&end=` — events, unique users, sessions, page views.
- `GET /telemetry/top?dimension=&start=&end=&limit=&user_id=` — `dimension` is `paths`, `event_types`, `users`, `controls` (tabs, buttons, menu items, dialogs and fields per page, with distinct users and phone uses), `api` or `api_users`. `user_id` narrows the counts to one person.
- Both take `event` (a slug) to count one event's rows only.

### Notifications (`/api/notifications`) · `notifications.py`
Your own settings; any member. Schemas in [`api/schemas/notifications.py`](../../api/schemas/notifications.py).
- `GET /preferences` · `PUT /preferences` — tournaments you follow.
- `GET /messages` — `MyNotificationsResponse {preset, changed, messages}`: every message that can reach you, each with `notice`, `recipient`, `section`, `title`, `when`, `community`, `preset`, `override`, `mode` (the quieter of the community's setting and yours) and `changed`. Staff alerts only for people who can see the admin side.
- `PUT /messages/preset` — body `{preset}`: `everything`, `needs_me` or `quiet`; overrides stay.
- `PUT /messages/{notice}/{recipient}` — body `{mode}`, `null` back to the preset. Unknown notice or recipient is `400`.
- `POST /messages/reset` — clears your overrides, keeps the preset. See [notification-settings.md](../features/notification-settings.md#personal-choices).

### Notification settings (`/api/notification-settings`) · `notification_settings.py`
Which messages this community sends, to whom, and whether they ping — the Admin → Notifications grid. **Staff** for reads and writes (`require_staff` / `require_staff_write`), re-checked in `NotificationSettingsService`. Schemas in [`api/schemas/notification_settings.py`](../../api/schemas/notification_settings.py). See [notification-settings.md](../features/notification-settings.md).
- `GET /notification-settings` — every catalogue cell as a `NotificationSettingResponse`: `notice`, `group`, `title`, `when`, `section` (the subheading inside the group, e.g. `Match day`, or empty), `recipient`, `recipient_label`, `who`, `mode` (`off` / `silent` / `ping`), `default`, `changed`, and `staff_choice` (on `on_duty` / `admins` / `staff` rows, what those rows add up to on the page's Staff dropdown: `ping_all`, `ping_on_duty`, `silent`, `off`, or null for a mix no choice matches).
- `PUT /notification-settings/{notice}/{recipient}` — body `NotificationSettingUpdate {mode}`; returns the updated row. Choosing the default clears the override; an unknown notice or a recipient the message doesn't have is `400`. Audited as `notification_setting.changed`.
- `PUT /notification-settings/{notice}/staff-alerts` — body `StaffNotificationUpdate {choice}`; sets the message's staff rows together and returns them. `ping_all` = all three ping (all staff only fires when nobody is on shift), `ping_on_duty` = admins `off`, the others `ping`, `silent` / `off` = all three. A message with no staff rows, or `ping_on_duty` on one with no `on_duty` row, is `400`. One `notification_setting.changed` audit row with `recipient: staff_alerts` and the choices as `old` / `new`.
- `POST /notification-settings/reset` — clears every override; returns `NotificationSettingsReset {reset}` (how many went back). Audited as `notification_settings.reset`.

### Sent messages (`/api/sent-messages`) · `sent_messages.py`
- `GET /sent-messages` — every DM sent in the community, newest first, **Staff only**. Filters: `start`/`end` (UTC), `user_id` (recipient), `search` (text or recipient name), `problems_only` (blocked or failed), `event` (a slug; `counts` follow it too); paginate with `limit` (1–500, default 50) and `offset`. Response carries `total`, window `counts` (`sent`, `silent`, `off_or_blocked`, `failed`) and each message's per-channel result. `discord_id` is a number, like the rest of REST.

### Exports (`/api/exports`) · `exports.py`
Your own data exports ([data-exports.md](../features/data-exports.md)). Only the requester sees an export; anyone else gets `404`.
- `GET /exports` — yours from the last 3 days, newest first, as `ExportInfo` (`id`, `kind`, `title`, `status`, `progress_done`/`progress_total`, `error`, `filename`, `size_bytes`, `has_json`, `created_at`, `finished_at`, `expires_at`).
- `POST /exports/tournaments` — body `{tournament_ids: [..] | null}` (null = every tournament you may export). Staff or tournament admin; `202` with the queued export. `403` for a tournament you can't export, `400` when a limit is hit.
- `POST /exports/volunteers` — body `{start, end}` (UTC). Needs the `VOLUNTEERS` flag and volunteer management. `202`.
- `GET /exports/{id}` — one export's state.
- `GET /exports/{id}/download?format=file|json` — the ZIP (or `tournaments.json`) with `Content-Disposition`; `400` until it's ready.

### Audit (`/api/audit-logs`) · `audit.py`
- `GET /audit-logs` — paginated, **Staff only** (the log carries webhook receiver URLs, role grants and accommodation requests). Filters: `start`/`end` (UTC time bounds), `user_id`, `action_contains` (substring match on the action string), `event` (a slug); paginate with `limit` (1–500, default 100) and `offset` (default 0). Response includes the matching-entry `total`.

### System config (`/api/config`) · `system_config.py`
- `GET /config` · `GET /config/{key}` · `PUT /config/{key}` (Staff). A write is validated by key with the same rules as the admin page (dates, positive integers, booleans, comp tiers, station format, tournament hours as a JSON object, Discord invite links only); an unknown key or a bad value is `400`.

### Webhooks (`/api/webhooks`) · `webhooks.py`
- `GET /webhooks` (Staff) · `POST /webhooks` (Staff-write; response includes the signing `secret` once) · `GET/PUT/DELETE /webhooks/{id}` · `POST /webhooks/{id}/regenerate-secret` (returns a new secret once) · `GET /webhooks/{id}/deliveries`. Staff-managed outbound webhooks; see [webhooks.md](../features/webhooks.md). `event_ids` on create/update limits a webhook to those events (`null` = every event, `[]` is `400`, an archived or unknown event is refused); community-wide messages ignore it.

### Events (`/api/events`) · `events.py`
The community's events, by slug. Reads list what the caller may see (`EventService.visible_events`: drafts only for staff and that event's role holders); every write is Staff and audited by `EventService`.
- `GET /events` · `GET /events/{slug}` — `EventResponse {id, slug, name, status, is_default, starts_on, ends_on}`.
- `POST /events` — body `{name, slug?, starts_on?, ends_on?, copy_from?, copy?}`; `copy_from` is a slug and `copy` the setup groups (`tournaments`, `positions`, `stages`, `check_in`, `discord_roles`). `201` with the draft and what was copied or skipped.
- `PATCH /events/{slug}` — name, slug (drafts only), dates.
- `POST /events/{slug}/go-live` · `/make-default` · `/archive` (body `{new_default}` when archiving the default with another live event) · `/unarchive`.
- `DELETE /events/{slug}` — a draft with no matches; `204`.
- `GET /events/{slug}/settings` · `PUT /events/{slug}/settings/{key}` — the per-event keys (`SystemConfigService.EVENT_CONFIG_KEYS`), validated like `PUT /config/{key}`; a community-wide key is `400`.

### API tokens (`/api/tokens`) · `tokens.py`
- `GET /tokens` · `POST /tokens` · `DELETE /tokens/{id}` — manage your own tokens.

### Join requests (`/api/join-requests`) · `memberships.py`
The staff queue from Admin → Members. Every route is Staff (`can_grant_roles`, checked in `TenantMembershipService`).
- `GET /join-requests` — pending requests to join the token's community, oldest first.
- `POST /join-requests/{id}/approve` · `POST /join-requests/{id}/deny` — decide one. The requester is DMed either way. `400` once it has been decided, `404` for another community's request.

### Check-in desk (`/api/check-in`) · `check_in.py`
The `/checkin` desk's actions, behind `EVENT_CHECK_IN`. The rules are the desk page's, enforced in `CheckInService` / `CheckInDeskService`: staff and Check-in Desk volunteers work the desk; a desk volunteer reads only open events; walk-ups and checking in someone withdrawn are staff-only. Event setup stays in Admin → Check-in. See [event-check-in.md](../features/event-check-in.md).
- `GET /check-in/events` — the events you can work. `GET /check-in/events/{id}/roster` — every row with `badges`, `lanyard`, account link, `withdrawn_at`, check-in state and `dm_status` (the check-in test DM).
- `POST /check-in/events/{id}/sync` — pull Matcherino sales and comps now; returns the `SyncResult` counts.
- `POST /check-in/entrants/{id}/check-in` (`{despite_withdrawn?}`) → `{entrant, already}`. A repeat changes nothing and says `already: true`. A withdrawn row is `400` for the desk; staff may send `despite_withdrawn: true` (audited). `DELETE` on the same path undoes it.
- `PUT /check-in/entrants/{id}/user` (`{user_id}`) · `DELETE /check-in/entrants/{id}/user` — link or unlink the row's account.
- `POST /check-in/events/{id}/walk-ups` (`{user_id}` or `{name}`, `check_in?`) — staff only; `201`.

### Discord role mappings (`/api/discord-role-mappings`) · `discord_role_mappings.py`
- `GET /discord-role-mappings?guild_id=` (list, optionally per guild) · `POST` (create) · `DELETE /{id}` — manage Discord-guild-role → grant mappings (Staff). A `POST` body sets **either** `app_role` (community-wide) **or** `tournament_grant` + `tournament_id` (Tournament Admin / Crew Coordinator on one event); both or neither is a 400.

## Feature-flag gating

When the caller's tenant has not enabled a router's flag, the **whole router 404s** — as if the feature did not exist — rather than 403'ing. See [features/feature-flags.md](../features/feature-flags.md).

| Group | Flag |
|---|---|
| Triforce texts | `TRIFORCE_TEXTS` |
| Payouts | `PAYOUTS` |
| Volunteers | `VOLUNTEERS` |
| Race room profiles, Race rooms | `RACETIME_ROOMS` |
| SpeedGaming | `SPEEDGAMING_ETL` |
| Async qualifiers, Async qualifier live races | `ASYNC_QUALIFIERS` |
| Brackets | `BRACKETS` |
| Equipment | `EQUIPMENT` |
| Feedback | `FEEDBACK` |
| Check-in desk | `EVENT_CHECK_IN` |
| `GET /reports/volunteer-hour-trends` (that route only) | `VOLUNTEERS` |

Every other group stays open, including presets, seeds, racetime bots, discord events, reports, telemetry and service health. `GET /seeds/randomizers` filters on a different axis — the randomizer API credentials the tenant has configured, not a flag (see [seed-generation.md](seed-generation.md#per-tenant-credentials)).

## Online-tournament features

Two of these groups are **global / platform** resources gated by `require_super_admin` / `require_super_admin_write` ([`api/dependencies.py`](../../api/dependencies.py)), which check the global `SUPER_ADMIN` role (`UserRole` with `tenant=NULL`) rather than tenant STAFF. The tenant-role-gated groups (presets, sync, qualifiers) use the coarse `require_api_actor` / `require_write_actor` HTTP dep and let the service enforce the finer role (`PRESET_MANAGER` / `SYNC_ADMIN` / `QUALIFIER_ADMIN` beyond STAFF), so a sub-STAFF token with the right role is accepted rather than 403'd.

### Presets (`/api/presets`) · `presets.py`
Tenant-authored seed presets (service gate `can_manage_presets`).
- `GET /presets` (opt `?randomizer=`) · `GET /presets/selectable` · `GET /presets/{id}`. The two picker reads (`?randomizer=`, `/selectable`) are open to any member but return `settings: {}` unless the caller can manage presets.
- `POST /presets` · `PATCH /presets/{id}` · `DELETE /presets/{id}` · `POST /presets/import-builtins` (import the built-in preset files).

### Race room profiles (`/api/race-room-profiles`) · `race_room_profiles.py`
Reusable racetime room settings (service gate `can_manage_sync`).
- `GET /race-room-profiles` · `/selectable` · `/{id}`; `POST` · `PATCH /{id}` · `DELETE /{id}`.

### Racetime bots (`/api/racetime-bots`) — **super-admin, global** · `racetime_bots.py`
Platform-managed racetime bots (no tenant FK). Responses are the secret-free
`RacetimeBotService.serialize(bot)` projection — `client_secret` is never returned.
- `GET /racetime-bots` · `/active` · `/{id}` (super-admin).
- `POST` · `PATCH /{id}` · `DELETE /{id}` (super-admin write).
- `GET /racetime-bots/{id}/grants` · `POST /{id}/grants` (`{tenant_id}`) · `DELETE /{id}/grants/{tenant_id}` — tenant authorization grants.
- `POST /racetime-bots/{id}/restart` — drop and reconnect the bot's racetime connection (`RacetimeBotService.restart_connection`; 400 when `RACETIME_BOT_ENABLED` is off).

### Platform (`/api/platform`) — **super-admin, global** · `platform.py`
The `/platform` page's other tabs. Reads use `require_super_admin`, writes
`require_super_admin_write`; communities are named by id and include switched-off
ones. Composite reads come from `PlatformAdminService`; writes go through the
owning service (`TenantService`, `FeatureFlagService`, `PasswordAuthService.set_active`,
`LogLevelService`). Passwords and account creation stay on the page.
- `GET /platform/communities` · `/{id}` (setup checklist + Staff); `POST` · `PATCH /{id}` (`null` clears `domain`/`discord_guild_id`; `is_active` switches it off).
- `POST /platform/communities/{id}/staff` (`{user_id}`) — first-admin grant (`TenantService.bootstrap_staff`).
- `GET /platform/communities/{id}/features` · `PUT /{id}/feature-group` (`{group_id|null}`) · `PUT /{id}/features/{flag}` (`{available: true|false|null}`).
- `GET /platform/users` (`q`, `has_password`, `is_active`, `limit`) · `/{id}` · `PUT /{id}/active` (`{is_active}`).
- `GET /platform/feature-groups`; `POST` · `PATCH /{id}` · `DELETE /{id}`.
- `GET /platform/log-levels` · `PUT` (`{logger, level}`) · `DELETE ?logger=`.

### Race rooms (`/api/race-rooms`) · `race_rooms.py`
- `GET /race-rooms/open` (Staff or Sync admin, checked in the service; filtered to your tenant) · `GET /race-rooms/by-match/{match_id}`.
- `POST /race-rooms` (`{match_id}`, manual create) · `POST /{id}/cancel` (`{reason?}`) · `PATCH /{id}/status` (`{status}`). Cancel/status go through `cancel_room_as` / `set_status_as`, which check `can_manage_sync`; the plain transitions stay ungated for system paths (match cancellation, the racetime handler). System internals (`get_by_slug`, `record_finish`, websocket event dispatch, auto-create) are **not** exposed.

### SpeedGaming (`/api/speedgaming`) · `speedgaming.py`
SpeedGaming schedule ETL event links (service gate `can_manage_sync`).
- `GET /speedgaming/links` · `GET /links/{id}/episodes` (episode `payload` blob omitted).
- `POST /links` · `PATCH /links/{id}` · `DELETE /links/{id}` · `POST /links/{id}/sync` (returns the `SyncResult` tallies).

### Discord events (`/api/discord-events`) · `discord_events.py`
Discord Scheduled Events mirror (service gate `can_manage_sync`).
- `GET /discord-events/tournaments` (per-tournament opt-in settings) · `GET /discord-events/events` (mirrored events).
- `PATCH /discord-events/tournaments/{id}` (settings) · `POST /discord-events/reconcile` (returns the `ReconcileResult` tallies).

### Service health (`/api/service-health`) · `service_health.py`
External-dependency health board (the HTTP dep is the only authz; the service does not re-gate).
- `GET /service-health` — tenant subset (Staff).
- `GET /service-health/board` — full snapshot (super-admin).
- `POST /service-health/refresh` — force a refresh (super-admin write; always `alert=False`, so an API call never DMs).

### Seeds (`/api/seeds`) · `seeds.py`
- `GET /seeds/randomizers` — the randomizers this community can actually roll + their `supports_triforce_texts` flag. A key-gated backend (`ootr`, `smmap`, `dk64r`) appears only once the community has configured its credential.
- `POST /seeds` (`{randomizer, preset_id?}`) — roll a seed (loads the tenant-scoped preset when given; unsupported randomizer → 400; honors `MOCK_SEEDGEN`). Staff and Preset managers only (`AuthService.ensure_can_roll_free_seed`, the people Seed Presets' Test roll is offered to); anyone else → **403**. A key-gated randomizer the community has not configured → **400** naming the missing credential.
- **Asynchronous randomizers** (`dk64r`) answer **202** with `{task_id, randomizer, status}` instead of a permalink: their upstream is a task queue and a roll takes minutes, so there is no url yet. Poll `GET /seeds/tasks/{id}` until `status` is terminal (`succeeded` / `failed` / `abandoned`); `url` is filled in only for a task that rolled for a match. Synchronous randomizers are unchanged (**200** + `url`).
- `GET /seeds/tasks/{id}` — where a queued roll has got to. A task another community owns → **404**.
- `POST /matches/{id}/seed` keeps its shape for both: an asynchronous roll returns `seed_url: null` with a message saying it is under way, and the players are DMed when it lands.
### Async qualifiers (`/api/async-qualifiers`) · `async_qualifiers.py`
Self-paced permalink-pool qualifiers (mixed auth: admin reads/writes gate
`can_admin_qualifier`; player run methods enforce ownership; `/open` and `/{id}/public`
are public-but-authenticated; the leaderboard is hidden while the window is open for non-admins).
- **Reads:** `GET /async-qualifiers` · `/open` · `/{id}` · `/{id}/public` · `/{id}/admins` · `/{id}/pools` · `/{id}/pools/available` · `/{id}/review-queue` · `/{id}/leaderboard` · `/{id}/me/runs` · `/{id}/me/active-run` · `/runs/{run_id}/notes`.
- **Qualifier/admin/pool/permalink writes:** `POST /async-qualifiers` · `PATCH`/`DELETE /{id}`; `POST`/`DELETE /{id}/admins[/{user_id}]`; `POST /{id}/pools`, `PATCH`/`DELETE /pools/{pool_id}`; `POST /pools/{pool_id}/permalinks` (+ `/bulk`, `/roll`), `PATCH`/`DELETE /permalinks/{permalink_id}`.
  Every permalink write validates the URL (http(s) with a host) and 400s otherwise —
  a runner's slot is spent the moment a permalink is revealed, so a typo costs a run.
  `/bulk` skips a bad line rather than failing the batch and answers
  `{created: [...], rejected: [{line, value, reason}]}`, where `line` is the 1-based
  position in the submitted `urls`.
- **Player run lifecycle:** `POST /{id}/runs` (start) · `POST /runs/{run_id}/submit|forfeit|reattempt`.
  `submit` 400s when the claimed `elapsed_seconds` exceeds the wall clock since the
  server stamped `started_at` — a run cannot have taken longer than it has existed.
  The run stays in progress, so the client can correct the time and resubmit. Every
  successful submit records `measured_seconds` (that wall clock) on the run response
  beside the runner's claim.
- **Review:** `POST /runs/{run_id}/claim|release|review` · `POST /runs/{run_id}/grant-reattempt`
  (admin; voids a runner's terminal run ignoring their `allowed_reattempts`, reason
  required) · `GET /{qualifier_id}/runs?limit=&offset=` (admin; the review queue holds
  only finished+pending rows and a forfeit is written straight to approved, so this is
  the read that reaches one). **Paginated** — `limit` 1–500 (default 100), `offset`
  default 0 — returning `{total, limit, offset, items}`, the same envelope as
  `GET /audit-logs`. Newest first, ordered `-created_at, -id` so a page boundary is
  stable across two runs created in the same second.
  `review` 400s on a rejection with a blank `note` — the reason is stored as a run note
  and DM'd to the runner. An approval's note stays optional.

### Async qualifier live races (`/api/async-qualifiers/live-races`) · `async_qualifier_live_races.py`
Synchronous racetime races for a qualifier pool (service gate `can_admin_qualifier`).
- `GET /async-qualifiers/live-races?qualifier_id=` · `/{id}` · `/{id}/runs`.
- `POST /async-qualifiers/live-races` (create; `permalink_id` may be null) · `POST /{id}/permalink` (`{permalink_id}` — set the pool permalink the race is scored against; refused once the race has runs) · `POST /{id}/open-room` · `POST /{id}/record` · `DELETE /{id}` (cancel). The room's own lifecycle events (`mark_in_progress`, the inbound `record_finish`) are **not** exposed — they arrive from racetime. `/{id}/record` is the manual counterpart, for when that event never came: a body of `{results: [{user_id, status, elapsed_seconds?}]}` where `status` is `finished` (time required), `forfeit` or `disqualified` (time ignored). It applies the same rules as the racetime path — no permalink is a 400, a racer over the pool cap is recorded voided, par recomputes, the race moves to FINISHED — and audits as a manual record. `GET /{id}` reports `unmatched_handles`: racetime accounts the last capture could not match, whose results were therefore not recorded.

### Brackets (`/api/brackets`) · `brackets.py`
Native tournament brackets. Reads take any token and are tenant-scoped in-service; writes reject read-only tokens at the HTTP layer and re-gate in `BracketService` — **Staff** everywhere except `POST /matches/{id}/games`, which also accepts the tournament's admins and the matchup's own two entrants (in a bracket-run tournament the bracket is the only way a player schedules at all). Thin wrappers over the service; schemas in [`api/schemas/brackets.py`](../../api/schemas/brackets.py). Behaviour — formats, advancement, roster rules, series semantics, who may schedule — is documented in [brackets.md](../features/brackets.md).
- **Unpublished stages are staff-only.** For a non-staff token a DRAFT or CANCELLED stage reads as absent: left out of `GET /brackets?tournament_id=`, and `404` on `/brackets/{id}` and its `/matches`, `/open-matches`, `/entries` and `/standings`. A tournament with no published stage `404`s on `/brackets/entrants` too. The same `BracketService` viewer-aware reads the public pages use.
- **Reads:** `GET /brackets?tournament_id=` (stages) · `/brackets/entrants?tournament_id=` (roster) · `/brackets/{id}` · `/brackets/{id}/matches` · `/brackets/{id}/open-matches` · `/brackets/{id}/entries` · `/brackets/matches/{match_id}` (one matchup with its `games`; **404** to non-staff when its stage is draft or cancelled) · `/brackets/my-open-matches?tournament_id=` (the **caller's** OPEN matchups with a free game slot and both entrants user-linked — the peer of the Challonge unscheduled-match list) · `/brackets/{id}/standings` · `/brackets/advancing-preview?tournament_id=&from_stage_order=` (dry run of the advance, writing nothing; Staff only, like the advance itself).
- **Authoring / roster:** `POST /brackets` · `PATCH /brackets/{id}` (`{name?, stage_order?, config?, format?}`, DRAFT-only, omitted fields left alone; the **format** is editable because a DRAFT stage has no match graph) · `DELETE /brackets/{id}` (204, DRAFT-only) · `PUT /brackets/{id}/rounds` (`{rounds}` — the per-round `{best_of, scheduled_at, scheduled_end}` metadata, or null to clear — `scheduled_end` must be after `scheduled_at`, and the pair bounds match-time suggestions for the round; the one definition edit allowed **after** a stage starts, since round chrome never touches the graph) · `PATCH /brackets/{id}/seeds` (`{seeds: {entry_id: seed|null}}`, DRAFT-only; the whole resulting seeding is validated before any write, so a duplicate or `<1` seed changes nothing, and the new entry order is returned) · `POST /brackets/entrants` · `POST /brackets/entrants/import` (`{tournament_id}` — one linked entrant per enrolled player not already rostered, returning only the new ones; idempotent) · `PATCH /brackets/entrants/{entrant_id}/user` (`{user_id}`, null to unlink — allowed in any state, since the link is what makes the entrant schedulable and notifiable) · `POST /brackets/entrants/{entrant_id}/drop` · `POST /brackets/{id}/entries` · `DELETE /brackets/entries/{entry_id}` (204, DRAFT-only un-enrollment) · `POST /brackets/entries/{entry_id}/retire` (mark the entry DROPPED — allowed in any state; the played results stand while Swiss stops pairing them and advancement skips them).
- **Lifecycle:** `POST /brackets/{id}/start` · `POST /brackets/matches/{match_id}/result` (`{winner_entry_id, entry1_score?, entry2_score?, forfeit?}`) · `PATCH /brackets/matches/{match_id}/result` (same body — staff correction of an already-COMPLETE match) · `POST /brackets/{id}/complete` (optional `{tie_breaks: {entry_id: rank}}` — resolves entries the standings pass left sharing a rank, applied on both Swiss and round robin) · `POST /brackets/{id}/cancel` (abandon a started stage: terminal, no `final_rank`, no champion; rejected on a COMPLETE stage) · `POST /brackets/advance-stage?tournament_id=` (`{from_stage_order}`).
- **Series:** `PATCH /brackets/matches/{match_id}/best-of` (`{best_of}` — odd, or null to fall back to the round's value; rejected once games exist) · `POST /brackets/matches/{match_id}/games` (`{scheduled_date, scheduled_time, stage_id?, comment?}` — books the **next** game into a real `Match`; the game number is server-assigned, so call it once per game, and all N slots may be booked up front). `stage_id` is staff-only and 400s for an entrant rather than being silently dropped.
- **Linking (staff):** `POST /brackets/matches/{match_id}/games/link` (`{scheduled_match_id}`) attaches an already-created `Match` as the matchup's next game — for a match scheduled through the ordinary editor. Its players must be exactly the matchup's two entrants (400 otherwise), same tournament, not already linked. `DELETE /brackets/games/by-match/{scheduled_match_id}` detaches an unplayed one (404 when the match backs no game, 400 once it has a result); the `Match` itself is left in place.

Two response shapes are worth calling out because they differ from the UI's view of the same data:

- `BracketMatchResponse` has **no `match_id`** — a bracket match is a *series*, so its scheduled matches live in the nested `games` list (empty until a game is booked; rows are created lazily and unneeded ones are cancelled when the series clinches). It also carries `best_of`, `entry1_score`/`entry2_score`/`forfeit`, and a derived **`status`** — the cross-surface `MatchStatus` (`pending` / `unscheduled` / `scheduled` / `checked_in` / `live` / `awaiting_result` / `complete` / `needs_reschedule`) the web bracket paints and the Discord DM quotes, so a consumer sees `live` mid-race instead of re-deriving it from game timestamps. Populated on the two list reads (`/matches`, `/open-matches`); `null` elsewhere.
- `GET /brackets/{id}/standings` returns `[{group_number, rows}]` — round robin one group per pool (ascending, `null` last), Swiss a single `null` group. Each row carries `display_name`, `seed`, both drop levels (`status` = this stage's entry, `entrant_status` = the roster), live `rank`, `points`, `wins`/`draws`/`losses`/`byes`, the `tiebreakers` chain and `tied_with`, and the persisted `final_rank` (null until the stage completes; it can differ from `rank` when staff hand-resolved a tie). Rows come back in rank order and dropped entries stay listed. Elimination formats have no points table and **400** — read their placement from `/matches` and each entry's `final_rank`.

## Tests

Integration tests live in [`tests/api/`](../../tests/api/) as `test_<resource>.py`, roughly one per group. They use the in-memory SQLite `db` fixture and the helpers in [`tests/api_helpers.py`](../../tests/api_helpers.py) (full app + token-authenticated client; pass `roles=[Role.SUPER_ADMIN]` for a global super-admin token).

Every group covers the same baseline matrix plus its own resource-specific cases: happy-path read, `401` unauthenticated, `403` read-only-token write, cross-tenant isolation, and `403` for a role-less token.
