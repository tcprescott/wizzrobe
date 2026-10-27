# Engagement Telemetry

Captures **how people actually use the tool** — page views, feature
interactions, and a mirror of every domain event — so post-event analysis can
go beyond the in-app feedback form. It answers "which pages and
reports did people use, how often, and who was active?" rather than "what
deliberate admin action was taken" (that is [audit logging](audit-logging.md)).

Source: model `TelemetryEvent` in [`models/audit.py`](../../models/audit.py);
[`application/services/telemetry_service.py`](../../application/services/telemetry_service.py);
[`application/repositories/telemetry_repository.py`](../../application/repositories/telemetry_repository.py);
report at [`pages/admin_tabs/reports/telemetry.py`](../../pages/admin_tabs/reports/telemetry.py).

## Data captured

Every row is one `TelemetryEvent` (append-only; see the
[schema](../reference/data-model.md#telemetryevent)). The load-bearing columns:

| Column | Meaning |
|---|---|
| `tenant` | nullable FK (`SET_NULL`), stamped from the ambient tenant at write time — the event's tenant for domain rows, the page's for page views. `NULL` marks a platform-level row |
| `category` | `page` (a page load), `interaction` (a curated UI action), `domain` (a bus event), or `api` (an MCP tool call or REST request) |
| `event_type` | `page.view`, `report.viewed`, `table.preferences_saved` / `table.preferences_reset` / `table.preferences_reset_all`, or the `EventType` string for domain rows. `TelemetryEventType.REPORT_EXPORTED` (`report.exported`) is declared but nothing emits it yet |
| `path` | the route/report the event happened on (null for domain events) |
| `session_id` | the NiceGUI `app.storage.browser` id — lets a single user's activity be reconstructed as an ordered session |
| `user` | resolved actor FK (`SET_NULL`); `username` is also snapshotted into `details` so attribution survives a user deletion |
| `details` | JSON: page query params, event payload |

`user` is resolved even for **deactivated** accounts — telemetry is about who
did something, not who may still act — which is why capture resolves the user
directly rather than through `get_user_from_discord_id` (that helper hides
inactive users on purpose for authorization).

## Capture points

All capture is **best-effort and non-blocking**: a telemetry failure is logged
and swallowed so it can never break a page render or a mutating call. All of them
honor the `TELEMETRY_ENABLED` kill-switch.

1. **Domain-event mirror** — `TelemetryService.record_event` is registered on
   the [event bus](event-system.md) as an async subscriber in the `main.py`
   lifespan (`event_bus.subscribe_async(TelemetryService().record_event)`, no
   `event_types` filter). Every published `Event` becomes a `domain` row on the
   dispatch worker, off the request path. This piggybacks on events services
   already publish — no new instrumentation in the domain services.
2. **Page views** — the shared `protected_page` / `public_page` wrapper
   ([`middleware/auth.py`](../../middleware/auth.py)) records a `page.view` per
   page load, reading the session identity + browser id during page building and
   handing the write to `background_tasks.create` (rebound to the page's tenant
   so the row is tenant-stamped). `protected_page` rows always carry a user
   (`AuthMiddleware` redirects anyone else first); `public_page` rows — the
   bracket views, `/help`, `/event-info`, `/facts`, the room-seeds board —
   may have a null `user`/`username` and are attributed to `session_id` alone.
   Page params are bounded (15 keys, 120 chars each), and any param whose name
   contains `token`, `secret`, `password` or `code` is stored as `[redacted]`
   (`_tracked_params`). A route can record under a different path with
   `telemetry_path=` — the room-seeds board records `/room/seeds` so the token
   never lands in `path`. Home (`/`, `/home/*`) is a bare `@ui.page`, so it calls
   `record_page_view('/home', …)` itself once a tenant is resolved, recording
   every section under `/home` with the section and deep-link ids as params (the
   platform community picker on the bare host records nothing). The other bare
   `@ui.page` routes — `/platform`, the auth and OAuth pages, the MCP consent
   screen — bypass the wrapper and record no page view.
3. **Interactions** — `TelemetryService.track_interaction` is called for
   specific high-value actions. Currently wired: `report.viewed` in the
   reports dispatcher (fired only for an explicit `?report=` so a plain `/admin`
   load does not manufacture a view), and the three `table.preferences_*`
   events from `TablePreferenceService` when a viewer saves or resets a table
   layout (`details` carries the `table_key`). To add another, call
   `track_interaction(event_type=…, path=…, discord_id=…, session_id=…)` from the presentation layer, reading identity from
   `app.storage`.
4. **Tab switches** — `BaseLayout` records a `tab.view` (`TelemetryService.track_tab_view`)
   for the landing tab and every switch on a tabbed hub (Admin, Home, Volunteer).
   A switch only rewrites the URL (`ui.navigate.history.replace`), so without
   this the hub's page views say nothing past the tab someone landed on. `path`
   is the hub plus the tab (`/admin/match-schedule`), `target` the tab slug.
5. **In-page usage** — [`static/js/usage.js`](../../static/js/usage.js), loaded
   by `BaseLayout` through [`theme/usage_telemetry.py`](../../theme/usage_telemetry.py),
   batches four kinds of row and sends them as the `wiz_usage` event every 5s
   (and when the tab is hidden); `TelemetryService.track_ui_batch` stores them:
   - `ui.click` — any named control: button, link, menu item, tab, toggle,
     checkbox, chip, clickable list item, select. `details` notes the `kind`,
     the `dialog` and ⋮ `menu` it sat in, and the `table` key. A row button's
     "… for <row>" suffix is dropped so every row's ⋮ counts as one control.
   - `ui.dialog_opened` / `ui.dialog_closed` — by dialog title; a close carries
     `via`, the last button pressed inside it, or `dismissed` (Escape / outside).
     A dropdown's option popup on a touch screen (Quasar's `.q-select__dialog`)
     is not counted as a dialog, so phone and desktop count alike; a choice
     made in it is a `ui.click` of kind `option` whose `menu` is the dropdown's
     label. Rows before 2026-10-24 logged these popups as `(untitled dialog)`.
   - `ui.field_used` — a field whose value changed, by its label. **Never the
     value.**
   Each row carries `device` (`phone` < 600px ≤ `tablet` < 1024px ≤ `desktop`)
   and the raw `width` in `details`. Paths are normalised the same way on both
   sides (`/t/<slug>` dropped, numeric segments → `{id}`; `tests/theme/test_usage_telemetry.py`
   pins the match). The browser is untrusted: the service keeps only the four
   known types, bounds every string, takes at most 50 rows a batch, and a page
   stops forwarding after 5,000. The `/platform` chrome doesn't load the script.

6. **MCP and REST API calls** — `TelemetryService.track_api_call` writes one
   `api` row per call: `mcp.tool_call` from the tool wrapper in
   [`mcpserver/registry.py`](../../mcpserver/registry.py) (`target` = tool name,
   `path` = `/mcp`) and `rest.request` from `ServiceErrorRoute` in
   [`api/dependencies.py`](../../api/dependencies.py) (`target` =
   `METHOD /api/route/{template}`, never the filled-in path). `details.outcome`
   is `ok`, the MCP error code (`forbidden`, `not_found`, …) or the HTTP status.
   Arguments and bodies are never stored. Only authenticated REST requests are
   recorded (`resolve_token` stashes the actor on `request.state.api_actor`);
   `/api/health` and `/api/version` take no token. MCP tools that name no
   community (`whoami`, `list_tenants`, `platform_*`) land as platform rows, so
   a community's reads don't see them. The insert is awaited inline: one row per
   call, after the result is built.

Telemetry has its own table rather than reusing either neighbour: `EventType.ALL`
is an external webhook contract that high-volume page views would flood and
bloat, and `AuditLog` is a low-volume record of privileged actions that
behavioural signal would drown. Telemetry *consumes* the bus and keeps its own
append-only table with its own aggregation indexes.

## The report

`Reports → Engagement Telemetry` (Staff only). Reads are Staff-gated at the
service boundary (mirroring `WebhookService`); the page also pre-checks so a
non-Staff admin gets a clear message rather than a raw error. It shows, for the
selected date window:

- **KPIs** — total events, unique users, unique sessions, page views.
- **Leaderboards** — most-viewed pages (with distinct users), most-frequent
  events, most-active users (with distinct sessions; click to filter).
- **Raw event log** — filterable (category, path substring, user) and
  server-paginated, with expandable JSON details and CSV export.

Reads are scoped to the current tenant in `TelemetryRepository` (`NULL`
outside one, so the platform sees only platform rows). The same Staff-gated
summary and leaderboards are exposed over REST (`GET /api/telemetry/summary`,
`GET /api/telemetry/top?dimension=…`) and as the MCP tools `telemetry_summary`
and `telemetry_top`. Those two also take `dimension=controls`: one row per
(page, event type, `target`) with uses, distinct users, and how many came from
a phone. The in-app report has no controls view yet. `dimension=api` gives
one row per MCP tool, REST route or GraphQL root-field set (`surface`, `target`,
calls, distinct users, errors) and `dimension=api_users` one row per person per
surface (calls, distinct tools/routes, last call); the summary's `mcp_users` /
`rest_users` / `graphql_users` count distinct callers. A GraphQL row's target is
`graphql <root fields>` (e.g. `graphql events viewer`), taken from the schema
rather than the client's operation name. No in-app view for these either. Every `top` dimension also takes an
optional `user_id` that narrows the counts to one person, so `controls` with a
`user_id` answers "what did this person use most".

Aggregations run in the database (`GROUP BY` / `COUNT DISTINCT` via
`TelemetryRepository`), never by loading the table into memory.

## Retention

Rows older than about 13 months (396 days) are deleted once a day by
[`application/services/telemetry_retention.py`](../../application/services/telemetry_retention.py),
in every community, in batches of 5,000. Thirteen months keeps this year's event
and last year's side by side; at roughly 1.5M rows a year per busy community,
keeping everything would slow every report year on year.

## Configuration

`TELEMETRY_ENABLED` (default on) is a runtime kill-switch for **capture** — set
it to `0`/`false`/`no`/`off` to stop recording without a code change. Reads are
unaffected; the report just shows whatever was already captured. See the
[env-var table](../deployment.md#environment-variables).

## Tests

[`tests/services/test_telemetry_service.py`](../../tests/services/test_telemetry_service.py):
every capture path, the `ui.*` batch validation, the `controls` leaderboard (including the disabled no-op and anonymous fallback),
the aggregation SQL against SQLite, list/count filters, and the Staff-only read
boundary.
