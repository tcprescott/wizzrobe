# Admin Reports

A multi-report framework in the admin dashboard's **Reports** tab: filterable,
URL-driven tables with CSV export. `pages/admin_tabs/reports/__init__.py` holds
the `_REPORT_HANDLERS` registry and the `reports_page(report, **params)`
dispatcher (an unknown or absent `report` falls back to the dashboard); each
report is one module beside it, and `shared.py` supplies the page shell, the
date-range/tournament filters, the export button and the URL-param navigation.

| Report | Module | Shows |
|---|---|---|
| Dashboard | `dashboard.py` | Landing page: KPI summary + cards linking to each report. Takes a date window like every other report (absent ⇒ the event window); no CSV, since each KPI links to a report that already exports the data it comes from |
| Insights & Trends | `insights.py` | Crew participation, volunteer hours, tournament health and admin activity trended weekly/monthly across events (`AnalyticsService`). Defaults to the window `AnalyticsService.activity_extent` reports — floored at four weeks, capped near a year, most-recent slice — rather than a fixed trailing range, so the charts open on buckets that have data in them |
| Capacity Forecast | `capacity.py` | Concurrent players over a date range vs. configured capacity, as stacked off-stream (floor) and on-stream (`stage_id` set) bands whose top edge is the total. Busiest times ranks by off-stream, since stage matches have their own setups. Off-stage matches of a tournament with `uses_tournament_room` off are left out entirely |
| Match Operations | `match_ops.py` | Per-match start delay, duration, confirmation lag; per-tournament aggregates |
| Staff / Crew Activity | `crew.py` | Coverage by match and contribution (hours, assignments) by person. A stream candidate is short only against its own tournament's `required_commentators`/`required_trackers` (see [What "covered" means](#what-covered-means)) |
| Stage Utilization | `stages.py` | Per-stage scheduled hours, gaps, back-to-back transitions |
| Volunteer Coverage | `volunteers.py` | Per-shift filled vs. needed counts over a date range, highlighting understaffed shifts. Behind `FeatureFlag.VOLUNTEERS` — card, handler and `VolunteerScheduleService.coverage` all gate on it |
| Engagement Telemetry | `telemetry.py` | Page views, interactions and the domain-event mirror over a date window — KPIs, leaderboards, filterable raw log (Staff only; see [telemetry.md](telemetry.md)) |
| Audit Log | `audit.py` | Searchable, paginated view of every audited action with expandable detail rows (see [audit-logging.md](audit-logging.md)) |

The **Reports** tab is shown to staff, tournament admins and crew coordinators
(`pages/admin.py`); individual reports apply their own checks (Telemetry is
Staff-only). A report behind a flag (`_REPORT_FEATURES` — today only
`volunteers` → `VOLUNTEERS`) falls back to the dashboard when the flag is off.
Opening a specific report records a `report.viewed` telemetry interaction. The
same aggregations are reachable outside the page: REST under `/api/reports`
(`/capacity-forecast`, `/stage-utilization`, `/match-operations`,
`/crew-coverage`, `/matches-active-at`, `/tournament-health`,
`/crew-participation-trends`, `/activity-trends`, `/volunteer-hour-trends`) and
the matching read-only MCP tools.

## What "covered" means

The crew-coverage report's **gap** flag and the tournament-health score's
**coverage** component both come from one function,
`reporting_shared.is_crew_covered`, so the two dashboards cannot drift apart. It
compares a stream candidate's *approved* crew against the tournament's
`required_commentators` / `required_trackers` (Tournament edit → Entry &
administration → Stream crew; defaults `1`/`1`).

Crew shape varies by community: plenty of tournaments restream with commentary
and no tracker. Setting a role to `0` says so, its matches stop reporting a gap,
and the schedule board stops offering a **Sign up** for a role nobody will
staff — `CrewService.signup_crew` refuses it as well, since the REST route and
the Discord button reach past the hidden control. Withdrawing stays open at any
requirement, so a signup made before the setting changed is not stuck on the
match. A match with no tournament falls back to the defaults.

**Reports link out; they never act.** A report row carries an id to the surface
that already owns the action, with that surface's own authorization, rather than
duplicating a mutation into a read-only page. The admin tabs a report can link
to take their focus as a query param — Schedule `?match_id=`, Volunteer Shifts
`?day=` — and every URL is built by `admin_url`, re-exported for pages from
[`pages/admin_tabs/links.py`](../../pages/admin_tabs/links.py) (the builders live in
[`application/utils/app_links.py`](../../application/utils/app_links.py) so
notifications can address the same routes).

| Report | Row control leads to |
|---|---|
| Staff / Crew Activity | the match on the Schedule board (its crew cell approves the pending signup) |
| Match Operations | the match on the board; per-tournament aggregates re-filter this report |
| Capacity Forecast | the matches making a peak instant, on the board |
| Stage Utilization | a stage's matches, on the board |
| Volunteer Coverage | Volunteer Shifts at the understaffed shift's local day |
| Dashboard KPIs | the one report each number is computed from, over the same window |

`shared.enable_drill_link(table, columns, rows, url_for, enabled=…)` builds the
desktop cell **and** the mobile card's action in one call — `enable_mobile_grid`
skips the actions column when generating cards, so a `body-cell-*` slot alone is
invisible on a phone. `enabled` is the *destination's* predicate
(`AuthService.can_view_schedule_board`, `can_manage_volunteers`): a link the
viewer cannot follow is not rendered rather than rendered disabled. Row clicks
that **filter** keep their meaning; navigation is always its own control.

The two server-paginated logs (Audit, Telemetry) label the page they are showing
— `Showing 51–100 of 124 entries`, and the plain count when everything fits on
one page. CSV export stays per-page; its filename carries the page number.

Aggregation always happens in a service, never in the page: most reports call
`ReportsService`, Insights calls `AnalyticsService`, and the three that read one
subsystem call its owner (`VolunteerScheduleService`, `TelemetryService`,
`AuditService`).

**Insights edge rules** (`analytics_service.py`):

- The composite tournament-health score drops a component with no data (no
  stream candidates, say) and renormalizes the remaining `HEALTH_WEIGHTS`, so a
  tournament is never marked down for a dimension that doesn't apply.
- A trend range wider than `MAX_BUCKETS` (240) keeps only the most recent 240,
  and the query is clamped to the same window so the reported totals match the
  chart.

**CSV exports escape formula injection.** `application/utils/csv_export.py`
prefixes a `'` to any cell whose first non-whitespace character is `=`, `+`, `-` or `@`; numerics are left alone
(safe and expected in CSV). Covered by `tests/test_csv_export.py`.

**Adding a report:** create `reports/my_report.py` rendering inside
`shared.report_page_shell()`, with its filters in `shared.report_filter_bar()`
(each through `filter_field`) and each block in a `section_panel` whose CSV
button goes in the panel's `aside`. Paint the main chart series from
`chart_palette()`, never a fixed colour. Put its aggregation in
`ReportsService`, then register the handler in `_REPORT_HANDLERS` and link it
from the dashboard.

**See also:** [frontend.md](../reference/frontend.md#reports-subsystem-pagesadmin_tabsreports) —
the reports subsystem's UI internals.
