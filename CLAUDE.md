# Wizzrobe - Claude Development Guide

Wizzrobe is a FastAPI + NiceGUI application for managing tournament schedules, matches, users, and crew for Wizzrobe events. It uses Tortoise ORM with PostgreSQL, integrates with Discord for auth and notifications, and runs as a single Docker container.

This file is the lean, always-loaded guide: the behavioral rules to follow on every task. For anything expository (tech stack, full directory map, models, env vars, routes, workflows), read the docs below rather than guessing.

## Documentation map

**Full index: [docs/README.md](docs/README.md)** — start here when unsure where something lives.

| Topic | Doc |
|---|---|
| System overview, tech stack, directory map, startup | [docs/architecture.md](docs/architecture.md) |
| Active feature status, known issues, key files | [docs/current-state.md](docs/current-state.md) |
| Local setup, mock Discord, dev data, tests, CI | [docs/development.md](docs/development.md) |
| Docker, **env-var table**, GHCR, operations | [docs/deployment.md](docs/deployment.md) |
| Three-layer pattern in depth, with examples | [docs/refactoring-guide.md](docs/refactoring-guide.md) |
| All models, ERD, enums, match lifecycle, migrations | [docs/reference/data-model.md](docs/reference/data-model.md) |
| Every service & utility module | [docs/reference/services.md](docs/reference/services.md) |
| Pages, routes, tabs, dialogs, tables, styling | [docs/reference/frontend.md](docs/reference/frontend.md) |
| **UI style guide** — tokens, components, contrast, a11y rules, how to check | [docs/style-guide.md](docs/style-guide.md) |
| OAuth mechanics, route protection, `AuthService` | [docs/reference/authentication.md](docs/reference/authentication.md) |
| Bot singleton, DM queue, interaction handlers | [docs/reference/discord-integration.md](docs/reference/discord-integration.md) |
| REST endpoints & schemas | [docs/reference/rest-api.md](docs/reference/rest-api.md) |
| MCP server (`/mcp`), its OAuth flow & tool catalogue | [docs/features/mcp-server.md](docs/features/mcp-server.md) |
| Randomizer integrations & presets | [docs/reference/seed-generation.md](docs/reference/seed-generation.md) |
| In-process event bus (publish/subscribe) | [docs/features/event-system.md](docs/features/event-system.md) |
| Outbound webhooks (event subscriber) | [docs/features/webhooks.md](docs/features/webhooks.md) |
| Datetime/timezone implementation | [docs/timezone-handling.md](docs/timezone-handling.md) |
| Multitenancy (tenant context, `/t/<slug>`, query scoping, `/platform`) | [docs/features/multitenancy.md](docs/features/multitenancy.md) |
| Events inside a community (`Event`, event context, default event, `event_id`) | [docs/features/events.md](docs/features/events.md) |
| Per-tenant feature flags (two-tier availability + enable, gating) | [docs/features/feature-flags.md](docs/features/feature-flags.md) |
| Engagement telemetry (page views, interactions, event mirror) | [docs/features/telemetry.md](docs/features/telemetry.md) |
| Sentry wiring, error-level policy, runtime log levels | [docs/features/observability.md](docs/features/observability.md) |
| In-app help (`/help` articles, help icons, the safe markdown model) | [docs/features/help.md](docs/features/help.md) |
| In-app changelog (`/changelog`, the weekly "What's new" entries) | [docs/features/changelog.md](docs/features/changelog.md) |
| Discord bot, notifications, role sync, mock mode | [docs/features/discord.md](docs/features/discord.md) |
| Notification settings (catalogue of every DM, off/silent/ping per community, `send_dm(notice=)`) | [docs/features/notification-settings.md](docs/features/notification-settings.md) |
| Crew signup, match acknowledgment, watching | [docs/features/match-participation.md](docs/features/match-participation.md) |
| Online tournaments (presets, race rooms, qualifiers, SG sync) | [docs/features/online-tournaments.md](docs/features/online-tournaments.md) |
| start.gg brackets (mirror, booking, writeback, bracket-source contract) | [docs/features/startgg.md](docs/features/startgg.md) |
| Event check-in (Matcherino roster sync, account matching, the desk) | [docs/features/event-check-in.md](docs/features/event-check-in.md) |
| Single-worker constraint and the way out of it | [docs/scaling-roadmap.md](docs/scaling-roadmap.md) |
| Remaining per-feature docs | [docs/features/](docs/features/) |

## Architecture: three-layer pattern

Respect these boundaries — they are the most-violated and most-important rule in the codebase:

```
Presentation (pages/, theme/)  →  Service (application/services/)  →  Repository (application/repositories/)  →  Models (models/)
```

- **Presentation** renders NiceGUI, handles interaction, calls services, catches their errors and shows `ui.notify()`. No business logic; no ORM *writes*. Read-only ORM lookups for simple display are acceptable but repositories are preferred.
- **Service** enforces rules/validation, coordinates repositories, writes audit logs, sends Discord notifications. Raises `ValueError` for user-facing errors. Must not import NiceGUI.
- **Repository** is pure data access (CRUD, queries, `prefetch_related`). No business logic, audit, or notifications.
- **Entry surfaces** — `api/` (REST routers), `discordbot/` (Discord interaction handlers) and `mcpserver/` (MCP tools) are peers of the web UI presentation layer: they call services and may do read-only *load-or-404* model lookups (the sanctioned shape is `Tournament.get_or_none(...)` in `api/routers/tournament_actions.py`), but must **not** import `application.repositories` or reach through `service.repository.*`. Route reads through a service method (e.g. `get_user_from_discord_id`, `UserService.get_user_by_id`, `MatchService.get_by_id`). `enforce_architecture.py` classifies all three as presentation and enforces this.

See [docs/refactoring-guide.md](docs/refactoring-guide.md) for the full pattern and examples.

### Adding a new feature
0. **Ask the user whether this feature warrants a per-tenant feature flag.** Not every feature needs one — flags exist only for deliberately-gated subsystems (see [Feature flags](#feature-flags)). Always ask; if yes, gate it (page/tab/API/worker) and register the flag. Do **not** add flags retroactively to existing features unless asked.
1. Add/update model in the `models/` package (per-domain submodule; re-export from `models/__init__.py`) → `poetry run aerich migrate && poetry run aerich upgrade`
2. Update/create the repository in `application/repositories/`
3. Update/create the service in `application/services/`
4. Export from each package's `__init__.py`
5. Build/update the UI in `pages/` or `theme/`
6. **Extend `scripts/seed_dev.py`** so the new model/feature has at least one representative row (and, where useful, each meaningful state) in the dev database. The seed script is the fixture set the [`/ui-validation`](docs/development.md) browser loop and every dev environment run against — a feature the seed never creates is a feature no one can see in the running app. Keep it idempotent (`get_or_create`) and tenant-scoped like the existing rows.
7. **Add a changelog line** (see [Changelog](#changelog)).

## Changelog

**Every PR that changes what a player, crew member, volunteer or staff member
sees or can do adds its own file, `application/changelog/changes/YYYY-MM-DD-<topic>.md`
holding only bullets under a category heading (`### New`, `### Improved`,
`### Fixed`, `### Security` or `### Behind the scenes`), in the same PR.** Don't edit the current week's entry in
`changelog.md`: parallel PRs all appended to it and conflicted. The in-app "What's
new" page (`/changelog`) folds each file into the Monday-to-Sunday week its date
falls in. New features, changed workflows or permissions, user-visible bug fixes
and security fixes all count; refactors, tests, hooks, CI and docs-only changes
don't. Write for the reader, not the codebase. The [`changelog` skill](.claude/skills/changelog/SKILL.md) has the
format and the git commands for catching up; `tests/test_changelog.py` checks
the structure.

## Coding conventions

- **Async everywhere** — all DB calls and UI handlers are `async def`.
- **Type hints throughout.**
- **No direct ORM writes in UI** — always go through the service layer. Read-only display queries are OK.
- **Raise `ValueError` for user errors** in services; catch in UI and show `ui.notify(str(e), color='warning')`.
- **Audit important actions** (create/update/delete) via `AuditService`.
- **No comments explaining what code does** — only for a non-obvious constraint, workaround, or invariant.
- **Keep services stateless** — static methods or per-request instances.

**Audit logging:** action strings are namespaced `verb.object` (e.g. `match.created`) — use a constant from `AuditActions` (add one when introducing a new action), pass `actor: User` explicitly (never guard with `if actor:`), and pass `details` as a plain dict. Full conventions: [docs/features/audit-logging.md](docs/features/audit-logging.md).

**Event publishing:** after a service commits a change, fire-and-forget an event on the in-process bus so subscribers (webhooks, UI refresh) can react. When the change is also audited — the usual case — do both in one call rather than hand-rolling the pair (`check_dry_regressions.py` blocks a new `write_log` + `event_bus.publish` sequence):

```python
await self.audit_service.write_and_publish(
    actor, AuditActions.MATCH_CREATED, details, EventType.MATCH_CREATED,
    event_extra={'tournament_id': match.tournament_id},   # event-only routing keys
    # or event_details={...} for a wholly separate event payload
)
```

Use bare `event_bus.publish(Event.create(EventType.X, {...}, actor))` (`from application.events import Event, EventType, event_bus`) only where there is no audit row to pair with — a worker observation, or a publish that must fire on a path the audit deliberately skips. `EventType` names mirror `AuditActions` and are an **external contract** — add a member to `EventType` + `EventType.ALL` for a new event; treat renames as breaking. `publish` is synchronous, never raises, and never blocks. Lives at `application/events/` (a peer of `services/`, so it's exempt from the architecture hook). Its narrow sibling `application/events/match_live.py` is **not** the same thing: that one carries only `(match_id, change_type)` to nudge open UI views. Publish domain events on `event_bus`; use `match_live` only for UI refresh. Detail: [docs/features/event-system.md](docs/features/event-system.md).

**An outside automation consumes six webhook events in production** (`match.rescheduled`, `match.stage_assigned`, `match.stage_cleared`, `match.deleted`, `match.cancelled`, `crew.approval_changed`). `tests/services/test_webhook_consumer_contract.py` freezes their payloads, the envelope, the headers and the signature. If it goes red, don't edit the expected values to get green: say so in the PR description and tell Thomas, so the automation's author hears first.

## Hand-maintained registries

**A new component should register itself where it's defined, not depend on someone remembering a second list elsewhere.** Prefer a registry the component's own module fills in (`mcpserver.registry.register`, `register_view_factory`, the notification `CATALOG`, `run_worker_loop`) or discovery from a folder (help articles, changelog files). A missed entry in a hand list fails silently: the worker never runs, the button does nothing, the page is never scanned.

- **Don't add a new hand-kept list** that has to mirror components defined elsewhere. If one is genuinely unavoidable (ordering matters, or the import itself is the registration), add a test in the same PR that fails when the list and the components disagree. `tests/test_registry_drift.py` is the pattern.
- **When you add one of these, also edit its list.** A red test or guardrail is the backstop, not the plan:

| Adding… | Also update | Caught by |
|---|---|---|
| a background worker (`run_worker_loop`) | `main.py` start **and** stop; `WORKERS` in `scripts/run_worker_tick.py` | tick script: `test_registry_drift.py`. `main.py`: nothing yet |
| a Discord button or DM view | `discordbot/__init__.py` (`register_interaction_handler` / `register_view_factory`) | `test_registry_drift.py` |
| an MCP tool *module* | `register_all` in `mcpserver/tools/__init__.py` | `test_registry_drift.py` |
| an `EventType` | `EventType.ALL` | `check_event_types.py` |
| a service or repository | its package `__init__.py` | `check_layer_exports.py` |
| a randomizer | every per-slug table in `SeedgenService`, plus `randomizer_credentials.py` | per-port tests only |
| a page | `scripts/ui_a11y_ci.json` | nothing yet |
| an outside-service client | a probe in `_PROBES` (`service_health_service.py`) | nothing yet |

Converting the workers, randomizers, a11y pages and health probes to self-registration is planned for after SG Live.

## Writing user-facing text

Scope: prose a person reads — chat replies, UI copy (labels, `ui.notify` text,
help and event-info articles), Discord notification copy, PR and issue comments.
Not identifiers, log lines, or the reference docs in `docs/`, which keep their own
table-and-bullet house style.

Be direct and have opinions. State the point first, then support it with specific
names and examples instead of vague claims. Trust the reader to recognise what
matters without being told it is significant or important. Use contractions.
Match tone to context: a casual question gets a casual answer.

Never use these words: *delve, dive into, navigate* (figurative), *underscore,
bolster, foster, harness, leverage, unpack, shed light on, pave the way, pivotal,
groundbreaking, cutting-edge, transformative, game-changing, innovative, robust,
comprehensive, seamless, intricate, nuanced* (as empty praise), *vibrant,
multifaceted, holistic, testament, landscape* (figurative), *realm*.

Never use these phrases: "In today's fast-paced/rapidly evolving/digital
world…", "It's important/worth noting that…", "One of the most
important/significant/crucial…", "When it comes to…", "At its core…", "At the end
of the day…", "This is where X comes in", "Let's break it down", "plays a crucial
role in…", "…cannot be overstated", "…underscoring the importance of…",
"…highlighting the need for…", "…reflecting a broader trend toward…", "…marking a
significant shift in…".

Never use these structures, which mimic insight without providing any: "It's not
just X — it's Y", "Not only X, but Y", "This isn't about X. It's about Y.", "No X.
No Y. Just Z."

Vary sentence and paragraph length rather than writing uniform blocks. Avoid the
"**Bold term**: explanation sentence" list format entirely; it is the single most
recognisable AI pattern. Don't signpost ("Let's explore", "Now let's turn to"),
don't open with a sweeping statement about the state of the world, don't close
with a summary or an inspirational wrap-up, and don't restate the question before
answering it. Plain prose usually beats headers and bullets. Skip preamble
("Great question!"), performative enthusiasm ("exciting", "incredible",
"powerful"), and unsolicited caveats. One em dash per response, maximum — commas
or parentheses otherwise.

Before finishing, read it back. Any sentence that sounds like a press release gets
rewritten, and any point made twice in different words gets made once.

## Calls to action must land on the control that performs them

**A notification that asks for something carries the route that does it — opening
the exact dialog, filter, or form, not a page from which the reader can go find
it.** This applies to every outbound surface: Discord DMs, web-push
notifications, and any in-app copy that names somewhere else to go.

Three failures to check for, in order of how often they happen:

- **Naming a destination instead of linking it.** "Approve it on the Users tab"
  makes the reader open the app, find the community, find the tab. Link the tab.
- **Linking a page rather than the control.** A bracket matchup DM that lands on
  the bracket leaves the reader hunting for their own row among thirty.
  `/home/player?schedule=<id>` opens the picker itself.
- **Linking a surface the recipient cannot act on.** The worst of the three,
  because it looks correct. Before choosing a target, check the role gate on
  what you are linking to: the bracket page's Schedule button is `is_staff`, so
  the matchup-ready DM used to send two players somewhere neither could book
  anything.

Build the link with `application/services/notification_links.py` (never a bare
path — a DM is read outside any request context, so it must be absolute and
tenant-qualified) and pass it as `send_dm(..., link=DMLink(...))`. That renders a
Discord **link button** beside any Acknowledge/signup buttons and becomes the
web-push notification's tap target in the same call. A markdown link in the
message text is **not** a substitute: `send_dm` omits the Discord content
entirely whenever an embed is present, so text-only links go unseen.

Deep-link params are the page's job to honour, and a stale one must say so — a
DM outlives the thing it points at, and a button that silently does nothing reads
as a broken app. Detail: [docs/features/discord.md](docs/features/discord.md#calls-to-action).

## UI styling and accessibility

**Read [docs/style-guide.md](docs/style-guide.md) before touching `pages/`, `theme/` or `static/css/`.** The app follows one design system ("Studio": neutral shell, one accent, one type family) and WCAG 2.1 AA, in light *and* dark mode. The rules that regress most:

- **Colour comes from tokens** (`var(--fg-muted)`, `--status-*`, `--wiz-link`) or classes (`.st-*`, `ui.badge(color=...)`), never a hex in a page. The accent is the community's colour, so never hard-code gold, and use it only for actions, links and "you are here".
- **Reach for the shared component**: `page_header()` for a page top, `section_panel()` for a block, `banner()` for a notice, `form_dialog()` and `dialog-card` / `--sm` / `--lg` for dialogs, a Settings pane card for a new admin setting.
- **Dark mode**: a `:root` token that aliases a dark-aware token must be restated in the `.body--dark, .q-dark` block, and overriding a Quasar `text-*`/`bg-*` class needs `@layer theme`.
- **Every control has a name** (`.tooltip()` on icon-only buttons, `aria-label` in slot templates) and text clears 4.5:1.
- **Actions follow one layout**: a page toolbar is one main button plus an Actions menu, a row or card is one button plus a ⋮ (`more_vert`) menu, destructive items come last and confirm first, and in any pair the negative action is on the left and the positive on the right. Match lists are run sheets (`MatchTableView(layout='run_sheet')`).
- **Mockups before building, and phone and desktop as one design**: see the guide's "Before you build".

`tests/theme/test_style_guide.py` and `tests/test_a11y_names.py` check the source; the CI `browser-a11y` job runs `scripts/ui_a11y_scan.js` (axe plus a tint-aware contrast check, light and dark) over `scripts/ui_a11y_ci.json`. Add a new page to that list.

## Timezone handling

**All datetimes are stored in UTC; user-facing times render on a per-request local clock.** Never store localized datetimes; never display raw UTC. Every conversion goes through `application/utils/timezone.py` (`parse_local_datetime`, `combine_local`, `local_day_bounds`, `format_local_time`/`_date`/`_display`, `now_local`, `today_local`, `to_local`, `to_utc_aware`, `timezone_label`).

Which clock is resolved once per page build and read back synchronously from `application/timezone_context.py` — the same contextvar + client-stash shape as `tenant_context.py`. A community either **pins** one zone (`Tenant.config['timezone']`, Admin → Timezone) or **follows each viewer** (their `User.timezone`, else the browser's zone from the `wiz_tz` cookie, else the community default).

Three rules that are easy to get wrong:
- **Every builder takes an optional `tz`; `tz=None` means "the current viewer".** Output that is *not* for the request's viewer must pass one explicitly — a cached/shared page, a tenant-anchored rule (tournament hours), a worker render. Use `TimezoneService.tenant_timezone_name()` + `tz_scope(...)`.
- **Discord gets native `<t:unix:F>` markup** (`discord_embeds.time_field`), never a formatted string — each recipient's client localizes it. **REST and webhooks stay UTC.**
- **A date derived from an instant moves with the zone.** Never bare `datetime.combine` (naive → read as UTC) or `date.today()`; use `combine_local` / `local_day_bounds` / `today_local`.

Full table, DST edges (nonexistent times raise), and storage notes: [docs/timezone-handling.md](docs/timezone-handling.md).

## Multitenancy

The app is **logically multitenant**: one DB, a `tenant` FK on nearly every model a community owns, tenant resolved per request from `/t/<slug>`. **Identity (`User`) is global; almost everything a community owns is tenant-scoped.** There is **no auto-scoping manager** — scoping is explicit:

- **Repositories** scope reads and stamp writes via `application/repositories/_tenant.py`: `scoped(Match.filter(...))` for reads, `Match.create(..., tenant_id=current_tenant_id())` for writes. A direct model read in presentation/service code hand-scopes: `Tournament.get_or_none(id=x, tenant_id=require_tenant_id())`.
- `require_tenant_id()` **raises** when no tenant is in scope — that loud failure is the safety net, not a bug to swallow. Any bot/worker/`background_tasks` path that touches scoped data must wrap it in `tenant_scope(tenant_id)` (`from application.tenant_context import tenant_scope`).
- **Roles are per-tenant** (`UserRole.tenant`); `AuthService` checks evaluate within `get_current_tenant_id()`. `SUPER_ADMIN` is the one global role (`tenant=NULL`) and bypasses the per-tenant role gate. Every `@protected_page` runs the membership gate (`enforce_membership`: a non-member gets the join page; `SUPER_ADMIN` bypasses); gated ones then authorize on tenant-scoped roles/tournament-admin/super-admin.
- When adding a tenant-scoped model: add the `tenant` FK, scope its repo, make formerly-global uniques composite with `tenant`, and add a leak test.

Detail: [docs/features/multitenancy.md](docs/features/multitenancy.md).

## Feature flags

Some subsystems are gated behind **per-tenant feature flags** — disabled by
default. Availability derives from a **live group/tier** (`FeatureFlagGroup` a
super-admin assigns per tenant on `/platform`; ungrouped tenants fall back to the
default group) with a tri-state per-tenant override on top; the community's
**STAFF** control enable (Admin → Features; available ⇒ on by default, opt-out
sticky). A feature is live when it is **available AND enabled**. Flags exist
**only** for deliberately-gated features — not one per feature.

```python
from application.services import FeatureFlagService
from models import FeatureFlag

if await FeatureFlagService().is_enabled(FeatureFlag.ASYNC_QUALIFIERS): ...
live = await FeatureFlagService().enabled_flags()   # set[FeatureFlag], one query
```

Gating a subsystem is **two obligations, and UI-only gating is not gating**:

1. **Hide it at the entry surfaces** — `@protected_page(feature=FeatureFlag.X)`
   for pages, `and FeatureFlag.X in live` on admin/home tabs,
   `require_feature(...)` on REST routers, an `is_enabled` skip in workers, and
   `AuthService.can_view_volunteer`-style resolution for any nav link *to* a
   gated page (never offer a link the gate will reject).
2. **Enforce it in the owning service** — `@requires_feature(FeatureFlag.X)`
   (from `application.feature_flags`) on the service's public entry methods:
   every mutation, plus the top-level reads that return the feature's data. It
   raises `FeatureDisabledError` (a `NotFoundError`, so REST 404s and UI
   `except ValueError` notifies). Without this the UI hides the feature while
   every other caller — a new page, a Discord handler, a worker, an un-mounted
   router — still reaches it.

Still an **authorization-style gate at the boundary**, never a DB read inside a
transaction; the guard reads a per-request cache, so repeated checks cost one
pass. Two deliberate carve-outs: a **soft integration point** called from an
unrelated flow (`push_result_if_linked`, the triforce-text embed) returns a
neutral value instead of raising, and a **worker** skips the tenant rather than
raising. Each flag declares its owning `service_modules` in its
`FeatureFlagSpec`; `check_feature_flag_gating.py` enforces both halves. A
super-admin does **not** bypass a flag.

**When adding a feature, always ask whether it warrants a flag** (step 0 above);
do not retrofit existing features.
Detail: [docs/features/feature-flags.md](docs/features/feature-flags.md).

## Authentication

Role-based via the `UserRole` junction table — there is **no** `permission` field on `User`. The `Role` enum has twelve members: the eight per-tenant community roles `STAFF`, `PROCTOR`, `STREAM_MANAGER`, `TRIFORCE_SUBMITTER`, `VOLUNTEER_COORDINATOR`, `EQUIPMENT_MANAGER`, `VOLUNTEER`, `CHECK_IN_DESK`; three per-tenant online-tournament admin roles `PRESET_MANAGER`, `SYNC_ADMIN`, `QUALIFIER_ADMIN` (each gates a management surface/worker the way STAFF gates the rest); and the one global platform role `SUPER_ADMIN` (its `UserRole` row carries `tenant=NULL`, checked via `AuthService.is_super_admin`, and bypasses the per-tenant role gate). Canonical list: `models.Role` / [docs/reference/authentication.md](docs/reference/authentication.md#roles). Identity lives in `app.storage.user` (`discord_id`). Use `AuthService`:

```python
from application.services import AuthService, get_user_from_discord_id
from models import Role

user = await get_user_from_discord_id(app.storage.user.get('discord_id'))  # User | None
await AuthService.has_role(user, Role.STAFF)        # bool
await AuthService.get_roles(user)                   # set[Role]
await AuthService.can_view_admin(user)              # any admin role / membership
```

Protect routes with `@protected_page('/path', roles=[Role.STAFF])` (the `roles=` kwarg is optional). A spectator surface that must work signed out uses `@public_page('/path')` instead — same tenant resolution and feature gate, but the route never joins `protected_routes`, so `AuthMiddleware` does not redirect to `/login`; the page body must tolerate `user is None` and everything it renders is world-readable (currently the bracket views, `/help`, `/changelog`, `/event-info`, `/room/{token}/seeds` and `/facts`). A `@public_page` skips the membership gate. Detail: [docs/reference/authentication.md](docs/reference/authentication.md).

## NiceGUI patterns

Fetch the authoritative API surface from `nicegui/llms.md` inside the installed package before writing NiceGUI code. The critical anti-patterns (from NiceGUI's official list) — keep these in mind:

- **Never `asyncio.create_task()` / `ensure_future()`** — bare tasks get GC'd and swallow exceptions. Use `background_tasks.create(coro())` (`from nicegui import background_tasks`).
- **Capture `context.client` before a background task that calls UI functions** (`ui.notify`, etc.) and restore it inside the task — the slot context is cleared in background tasks. Use `context.client` (`from nicegui import context`), **not** `Client.current` — the latter does not exist in NiceGUI 3.x and raises `AttributeError: type object 'Client' has no attribute 'current'` at runtime:
  ```python
  table.on('evt', lambda e: background_tasks.create(handle(e.args, context.client)))

  async def handle(row, client):
      with client:
          ...
          ui.notify('Done', color='positive')
  ```
  `Client` only supports the sync context manager (`with`), not `async with` — `nicegui.client.Client` has `__enter__`/`__exit__` only, so `async with client:` raises `TypeError: 'Client' object does not support the asynchronous context manager protocol` at runtime.
- **Never block the event loop** — all users share one loop. Use `async with httpx.AsyncClient()` (not `requests`) and `await asyncio.sleep()` (not `time.sleep()`).
- **Use `@ui.refreshable`** for dynamic sections; call `.refresh()` after data changes instead of rebuilding the page.
- **Module-level variables are shared across all users** — never store per-user state at module level; use `app.storage.user` or locals inside the `@ui.page` function.
- **Per-tab state must let go when the tab does.** A `WeakKeyDictionary[Client, X]` whose value holds the client (or any element, closure or subscription that does) never drops its entry, so every closed tab's page stays in memory and garbage-collection pauses grow with uptime (SGLMAN-29). Release it in `client.on_delete` (`app.on_delete` for a module-wide hook); a disconnect alone isn't enough. The browser CI step `scripts/ui_leak_check.js` (main only, not pull requests) fails a leak and prints what holds the page; run it locally on a change to per-tab state.
- **Every `ui.table` needs a mobile card view** — a bare table overflows a phone and hides its row-action buttons off-screen. Call `enable_mobile_grid(table, columns, actions=…, field_slots=…)` (`from theme.tables.mobile_grid import enable_mobile_grid`) right after building the table; it adds `:grid="Quasar.Screen.lt.md"` + a `.wiz-grid-card` `item` slot. The four family tables (match/user/tournament/equipment) keep bespoke `item` slots; a table that must stay a table opts out with a `# mobile-grid: exempt` comment. The `check_table_grid` hook enforces this. Detail: [docs/reference/frontend.md](docs/reference/frontend.md#data-tables).
- **Every `ui.table` also needs a `table_key`** — the per-viewer column layout (visible columns, order, widths, page size, density, wrap) rides on it. Pass `table_key=TableKeys.X` to `enable_mobile_grid`, which then owns the ordering that keeps the mobile card out of it; a bespoke `item` slot calls `customize_table(table, columns, key=…)` **after** registering that slot. Opt out with `# table-prefs: exempt — <reason>`. The `check_table_prefs` hook enforces this. Detail: [docs/reference/frontend.md](docs/reference/frontend.md#data-tables).
