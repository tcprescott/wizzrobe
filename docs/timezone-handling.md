# Timezone Handling

**Store UTC, display local.** Never persist a localized datetime; never render a
raw UTC one. Every conversion goes through
[`application/utils/timezone.py`](../application/utils/timezone.py), and *which*
local clock it renders on is resolved per request by
[`application/timezone_context.py`](../application/timezone_context.py) and
[`TimezoneService`](../application/services/timezone_service.py).

The app was hardcoded to US/Eastern until this layer existed. That was fine for
SpeedGaming Live — one venue, one clock — and actively wrong for an online
tournament whose players are spread across the world.

## Who decides the clock

Two tiers. The community decides whether it decides:

| Tenant mode | What members see |
|---|---|
| `pinned` | One zone for everybody — the community's own. Per-user preferences are ignored. |
| `user` | Each viewer's own clock: their profile preference → their browser's zone → the community's default. |

`pinned` is right when everyone is in the same room and "14:30" means 14:30 *at
the venue*. `user` is right when they are not.

Stored in `Tenant.config['timezone']` as `{'mode': …, 'name': '<IANA>'}` (the
documented home for per-tenant knobs, same as the theme palette). `name` matters
in **both** modes: pinned uses it as *the* clock, and `user` mode uses it as the
floor everything falls back to. Edited at **Admin → Settings → General**.

The personal preference is `User.timezone` (nullable IANA name; `NULL` means
"detect from my device"), edited on the profile tab. It is **global**, like the
rest of identity — one person carries one clock across every community.

## How the browser's zone gets to the server

A snippet installed by `theme/chrome.install_timezone_detection()` writes
`Intl.DateTimeFormat().resolvedOptions().timeZone` to the `wiz_tz` cookie;
`TenantMiddleware` reads it into the request context. A cookie rather than a
`ui.run_javascript` round-trip because the server renders times *before* any
websocket exists.

On a browser's first-ever visit there is no cookie, so that one page renders on
the community default and reloads once. A zone that later *changes* (someone
travelled) updates the cookie silently and applies on the next navigation — a
surprise reload mid-session is worse than a few seconds of stale clock.

## 12- or 24-hour

The same snippet writes the browser's preferred clock to `wiz_hc` (`12` or
`24`, from `Intl.DateTimeFormat([], {hour: 'numeric'}).resolvedOptions()`).
It never reloads by itself: a first visit's zone reload carries it, and a
browser that already had `wiz_tz` picks it up on its next navigation.
`User.hour_cycle` (Profile → Time format) outranks it; nothing known means
24-hour. `_bind_display_timezone` binds the answer beside the zone (contextvar
plus `app.storage.client['hour12']`), and `capture_render_context` carries it
into detached tasks.

Every `%H:%M` that goes through `format_local_datetime` (so every
`format_local_*` builder) becomes `9:30 PM` for a 12-hour viewer; use
`strftime_local` for a datetime already on the wall clock and
`format_local_hour` for hour-grid labels. Values for `<input type=time>` /
`datetime-local` stay `%H:%M` (a format containing `T%H` is never rewritten).
Sortable table columns get a clock-aware `:sort` for 12-hour viewers
(`theme/tables/preferences.CLOCK_SORT`), since `10:00 PM` sorts before
`9:30 AM` as text.

**Unbound means 24-hour**, which is what workers, the bot, REST, MCP and the
static spectator pages get. Output that isn't for the viewer's own page forces
it with `hour_cycle_scope(False)`: notification copies
(`readable_discord_times`), CSV exports, and the shared Watch view cache.

## Resolution, and where it is bound

`middleware/auth.py::_tenant_page` resolves once per page build and writes the
answer via `bind_display_timezone` to both a contextvar (serving the rest of the HTTP request) and
`app.storage.client['tz']` (serving every later websocket event handler on that
connection, which runs after the request context is gone). This is the same
two-tier shape as `application/tenant_context.py`, for the same reason.

Reading it back is a free sync call, which is what lets ~390 formatting sites stay
argument-free.

Two places need it by hand, because they do not pass through `_tenant_page`:

- **The tenant home** (`pages/home.py`) is a bare `ui.page` — the same function
  also serves the tenant-less community picker — so it calls
  `bind_display_timezone(tid, user)` itself, next to the membership gate it
  likewise applies by hand. Without it the whole home surface, *including the
  timezone picker on the Profile tab*, renders on the fallback zone.
- **Detached background tasks** (`theme/tables/admin_crud.scoped_background`)
  have neither the contextvars nor the client stash — NiceGUI keys its slot
  stack by asyncio task id, so `app.storage.client` raises inside one. Capture
  with `capture_render_context()` at build time and rebind; the tenant and the
  zone travel together so the pairing cannot drift apart again.

## The utilities

`tz=None` (the default) means "the current viewer's zone". Pass an explicit `tz`
whenever the output is **not** for the current request's viewer.

| Function | Direction |
|---|---|
| `parse_local_datetime(date_str, time_str, tz=None)` | user input (wall clock) → UTC, for storage |
| `combine_local(date, time, tz=None)` | `date`+`time` objects → UTC (the programmatic twin) |
| `local_day_bounds(start, end, tz=None)` | a picked date range → half-open UTC query bounds |
| `format_local_datetime(dt, fmt, tz=None)` | UTC → local, custom format |
| `format_local_date(dt, tz=None)` | UTC → `YYYY-MM-DD` |
| `format_local_time(dt, tz=None)` | UTC → `HH:MM` (or `9:30 PM`, see above) |
| `format_local_hour(dt, tz=None)` | UTC → hour-axis label, `21:00` or `9 PM` |
| `format_local_time_range(start, end, tz=None)` | UTC pair → `HH:MM–HH:MM EDT` (label taken at `start`) |
| `format_local_display(dt, tz=None)` | UTC → `YYYY-MM-DD HH:MM EST` |
| `timezone_label(tz=None, at=None)` | the zone's short label, for captions |
| `now_local(tz=None)` / `today_local(tz=None)` | current time / current date, local |
| `to_local(dt, tz=None)` | any datetime → local |
| `to_utc_aware(dt)` | any datetime → UTC-aware (**normalization, takes no `tz`**) |

```python
from application.utils.timezone import parse_local_datetime, format_local_display

scheduled_at = parse_local_datetime("2026-01-15", "14:30")   # viewer's clock → UTC
format_local_display(scheduled_at)                           # → "2026-01-15 14:30 EST"
```

## Which clock, for output that isn't a page

Ask whether the artifact has a single identifiable reader.

**No reader, or many** → the **tenant's** zone via
`TimezoneService.tenant_timezone_name()`, bound with `tz_scope(...)`. There is no
one whose clock would be the right answer, so the community's is:

- **Cached spectator pages** (`pages/static_brackets.py`) — one render, every viewer.
- **Tournament operating hours** (`MatchService.assert_within_tournament_hours`,
  `match_suggestion_service`) — the community's rule, written in the community's
  clock. Resolving it per viewer would give two people opposite verdicts on the
  same instant.
- **MCP tools** (`mcpserver/registry.py`) — no browser and no viewer, but tools
  still take calendar dates.
- **REST** (`api/dependencies.py`) — responses stay UTC (see below); the binding
  exists so date-valued params land on the community's day, not UTC's.
- **Workers** (`for_each_tenant_scoped`) — bound alongside `tenant_scope`, so a
  reminder DM does not tell every community the same wrong time.
- **Shift generation** from the standard blocks — "Shift 1 is 08:00" is a fact
  about the event, not about the coordinator generating it.

**One reader, and it is the requester** → the ambient zone; that *is* their
clock. Pages, report windows (`display_bounds`), and exports the viewer
triggered. An export also **labels** the zone it used (`timezone_label()`),
because a CSV can be forwarded to someone who did not generate it.

**One reader, and it is someone else** → **Discord sidesteps the question**:
embeds and DMs use native `<t:unix:F>` markup (`discord_embeds.time_field`),
which each recipient's own client renders in their own zone. Never format a
literal time string into Discord output.

## Time inputs say which clock they mean

Every native date/time control comes from `theme/dialog/_helpers.py`
(`native_date_input` / `native_time_input` / `native_datetime_input`), and the
time ones carry the zone in their label — `Time (EDT)`. A bare "Time" field was
unambiguous only while the whole app ran on one clock.

`tz=None` labels it with the viewer's own zone, matching the `parse_local_datetime`
that will read it. Pass an explicit `tz` where the field is **not** read on the
viewer's clock — tournament operating hours are the community's rule, so
Settings → Event (`pages/admin_tabs/settings/event.py`) captions that grid with the tenant's zone instead.
`show_timezone=False` opts out where nearby copy already says it.

Four fields used to hand-roll the `props('type=time …')` string. Not by intent:
the helper could not express `dense`, so anything wanting a compact inline row
wrote its own — and a hand-rolled field is one the zone label can never reach.
The helpers now take `dense`/`stack_label`, and
`.claude/scripts/check_native_datetime_inputs.py` blocks a raw `type=date`/`time`/
`datetime-local` props string anywhere in `pages/` or `theme/`.

## REST and webhooks stay UTC

Timezone is presentation; a programmatic client gets one canonical instant.
`Event.occurred_at` and every webhook payload are UTC ISO-8601 and are an
**external contract** — do not localize them.

## DST

`zoneinfo` handles offset transitions; there is no manual offset arithmetic
anywhere in the codebase and there should not be. Two edges are handled
explicitly in `parse_local_datetime`:

- **Nonexistent times** (the hour skipped by a spring-forward) **raise
  `ValueError`.** Accepting them silently stored a time an hour off from the one
  the user typed.
- **Ambiguous times** (the hour repeated by a fall-back) resolve to the *first*
  occurrence. There is no UI to express "the second 01:30".

Both are zone-specific, and dynamic zones make that sharper: a zone with no DST
at all (`Asia/Kolkata`) has neither edge. `tests/test_timezone.py` covers these
plus half-hour offsets and southern-hemisphere DST.

## Date boundaries are the sharp edge

A calendar date derived from an instant **moves with the zone** — at 21:00 in New
York it is already tomorrow in London. Anything deriving a date, or building a
date-range filter, must do so on an explicit clock:

- Use `local_day_bounds` / `combine_local`, never bare `datetime.combine` (which
  yields a naive value the ORM reads as UTC — a silent whole-window shift).
- Use `today_local()`, never `date.today()` or `datetime.now().date()`.

`.claude/scripts/enforce_datetime_safety.py` blocks the naive-`now` forms.

## Storage

The `*_at` columns were created as `TIMESTAMPTZ` in the initial schema, so stored
values have always been timezone-aware UTC. There is no legacy naive data and no
shift-by-N-hours correction to apply. Tortoise runs with `use_tz` unset, so reads
always come back UTC-aware and the display layer is the only place conversion
happens.

## Rollout

Migration 47 pins every **existing** community to `America/New_York`, so
behaviour was unchanged on deploy and going dynamic is a deliberate admin action.
**New** communities default to `user` mode.
