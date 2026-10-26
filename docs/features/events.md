# Events

A community (tenant) holds **events**: one SGL weekend, one season. The
community keeps what lasts year to year (members, staff, integrations, keys,
presets, equipment, webhooks, notification settings, feature flags); an event
owns what belongs to one run of it (tournaments and their matches, brackets,
stages and stations, volunteer positions and shifts, check-in, meetups,
qualifiers, the event guide).

The full design, with every decision Thomas made, is the spec
"Events in Wizzrobe" (Claude Doc). It lands in six phases; this page describes
what is built.

| Phase | What | Status |
|---|---|---|
| 0 | `Event` model, event context, default-event binding, migration + backfill | built |
| 1 | Repositories read and stamp the event; workers loop live events; NOT NULL; leak tests | built |
| 2 | `/e/<slug>` URLs, switcher, admin menu split, Admin → Events, copy setup | planned |
| 3 | Archive = read-only, enforced in services | planned |
| 4 | Per-event roles | planned |
| 5 | REST + MCP `event` argument, webhook event filter, docs | planned |

## The model

`models/event.py`: `Event(tenant, name, slug, status, is_default, starts_on,
ends_on, config, archived_at, archived_by, pii_purged_at)`.

- `status` is `EventStatus.DRAFT | LIVE | ARCHIVED`. Any number can be live.
- Exactly one event per community is the **default**, the target of a bare
  link. Tortoise can't express a partial unique index, so the migration creates
  `uid_event_one_default_per_tenant` and `EventService` keeps it true.
- `slug` is unique within the community (`unique_together = (tenant, slug)`).
- **Every community has at least one event.** `TenantService.create_tenant`
  creates the first one (live, default, named after the community) in the same
  transaction; seed scripts call `EventService.ensure_first_event`.

### Which rows carry `event_id`

| Kind | Models | On delete |
|---|---|---|
| Event-owned | `Tournament`, `Match` (denormalised from its tournament), `Stage`, `Station`, `VolunteerPosition`, `VolunteerShift`, `VolunteerAvailability`, `PlayerAvailability`, `VolunteerRoleMapping`, `CheckInEvent`, `CommunityEvent`, `CommunityEventSeen`, `AsyncQualifier`, `DiscordRoleRule`, `DiscordScheduledEvent`, `RoomToken`, `WatchLayoutVote`, `AccommodationRequest`, `EmergencyContact` | CASCADE |
| Community-owned, tagged | `AuditLog`, `TelemetryEvent`, `SentMessage`, `StaffRequestDM`, `Feedback`, `EquipmentLoan`, `DataExport`, `DiscordRoleGrant` | SET NULL |

Children of these (match players, crew, brackets, shift assignments, check-in
entrants…) reach their event through their parent and get no column.

On event-owned models the column is **NOT NULL** (migration 134); on tagged
models it stays nullable, because a row can happen outside any event (a
platform action, a community-wide DM).

### Per-event uniques

Names and one-per-person rows are unique within an event, so next year's event
can reuse this year's: `Stage`, `Station` and `VolunteerPosition` →
`(tenant, event, name)`; `CheckInEvent` → `(tenant, event, matcherino_venue_id)`;
`DiscordRoleRule` → `(tenant, event, key)`; `AccommodationRequest`,
`EmergencyContact`, `CommunityEventSeen` → `(tenant, event, user)`;
`WatchLayoutVote` → `(tenant, event, session_id, layout)`.

## Event context

`application/event_context.py` mirrors `tenant_context.py`:
`get_current_event_id()`, `require_event_id()` (raises with none bound),
`event_scope(event_id)`, and a per-client stash so NiceGUI websocket handlers
see the event their page was built with.

`TenantMiddleware` binds the community's default event next to the tenant
(path mode and host mode), resolved by `EventService.default_event_id`, which
is cached per process and dropped on every event write. `@protected_page`,
`@public_page` and `/home` stash it on the client. A binding remembers the
community it was made in and reads as unbound under any other, so an event
never crosses a community wall.

The other binding sites:

| Surface | Event |
|---|---|
| REST (`api/dependencies.py`), MCP (`mcpserver/registry.py`) | the community's default, until phase 5 adds an `event` argument |
| Workers (`for_each_tenant_scoped`) | `event_id_of=`: the row's own event (a match, a shift, a qualifier, a check-in event), skipped unless live; `each_live_event=True`: a community sync runs once per live event |
| Discord DM buttons (`discordbot/_tenant.py`) | the event of the match, crew slot, shift, reschedule request or invite the button is about |
| DM queue, event bus, page-view telemetry, DM mirrors | the publisher's event, carried to the worker |
| Seed scripts (`scripts.seed_events.install_event_stamping`) | the community's default; direct creates take their parent's event |

### Reading and writing

`event_scoped(qs)` and `live_event_scoped(qs)` live in
`application/event_context.py` (so services and entry surfaces can use them,
as they hand-scope a tenant) and are re-exported from
`application/repositories/_event.py`. `event_scoped` filters on tenant **and**
one event, the ambient one unless the caller passes the row's;
`live_event_scoped` filters on every live event of the community, for personal
pages that follow a person across events (My Schedule, staff alert settings).

A lookup by id stays tenant-scoped only: **the row decides its own event.**

Writes go through `EventScopedRepository.create` or `stamp_event_id`: a model
in `EVENT_PARENTS` takes its parent's event (`Match` from its tournament,
`VolunteerShift` and `VolunteerRoleMapping` from their position,
`DiscordRoleRule` from its tournament), everything else the ambient one. Tagged
rows take `tagged_event_id()`, which may be None.

`.claude/scripts/check_event_scoping.py` (hook and CI) flags a list read on an
event-owned model that names no event and no row key (`id`, or an FK to another
event-owned row), and a write that stamps none. A deliberate exception says
`cross-event` or `event-scope: exempt — <reason>` in its function.

Discord role sync (outbound) is per event, but the roles live on one server:
a sync combines the rules of every live event, and each grant is tagged with
the event of the rule that gave it.

## The migration

`migrations/models/133_*_events.py`, additive and invisible:

1. Creates `event` and the nullable `event_id` columns.
2. Gives every community one live, default event named after it, with its
   slug and the dates from its `event_start_date` / `event_end_date` settings
   (`sgl26`'s is named "SGL 2026").
3. A community with test tournaments (`Tournament.is_test`) also gets a
   "Testing" event, live (so workers keep running for test tournaments) but
   not the default, and those tournaments and their matches move there.
4. Backfills: matches from their tournament, shifts from their position, Discord
   role rules from their tournament, mirrored Discord events from their match,
   everything else from the community's default.
5. Indexes every `event_id`.

Take a database snapshot before running it on production: the backfill touches
the audit and telemetry tables in full.

`migrations/models/134_*_events_required.py` (phase 1):

1. Runs the backfill again for rows written between 133 and this deploy.
2. Clears the stage (and the players' stations) of a match whose stage belongs
   to another event: the Testing matches 133 moved off the default event.
3. Makes every event-owned `event_id` NOT NULL.
4. Swaps the community-wide uniques for the per-event ones above. The old ones
   are found by their columns, because two were created by hand under names
   Tortoise doesn't generate.
