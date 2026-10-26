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
| 1 | Repositories read and stamp the event; workers loop live events; leak tests | planned |
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

**The column is nullable for now, and nothing reads it yet.** Writes don't stamp
it until phase 1; the migration that makes it NOT NULL re-runs the backfill
first, so rows written in between are caught.

## Event context

`application/event_context.py` mirrors `tenant_context.py`:
`get_current_event_id()`, `require_event_id()` (raises with none bound),
`event_scope(event_id)`, and a per-client stash so NiceGUI websocket handlers
see the event their page was built with.

`TenantMiddleware` binds the community's default event next to the tenant
(path mode and host mode), resolved by `EventService.default_event_id`, which
is cached per process and dropped on every event write. `@protected_page`,
`@public_page` and `/home` stash it on the client. Workers, the bot and
background tasks bind nothing yet.

Repositories get `event_scoped(qs)` in `application/repositories/_event.py`
beside `scoped(qs)`. It filters on tenant **and** event, so a wrong event can
at worst show another event of the same community.

## The migration

`migrations/models/133_*_events.py`, additive and invisible:

1. Creates `event` and the nullable `event_id` columns.
2. Gives every community one live, default event named after it, with its
   slug and the dates from its `event_start_date` / `event_end_date` settings
   (`sgl26`'s is named "SGL 2026").
3. A community with test tournaments (`Tournament.is_test`) also gets a draft
   "Testing" event, and those tournaments and their matches move there.
4. Backfills: matches from their tournament, shifts from their position, Discord
   role rules from their tournament, mirrored Discord events from their match,
   everything else from the community's default.
5. Indexes every `event_id`.

Take a database snapshot before running it on production: the backfill touches
the audit and telemetry tables in full.
