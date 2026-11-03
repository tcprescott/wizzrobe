# Dev Seed Standard

*The rules `scripts/seed_*.py` follows, and what enforces each one. Part of the
[documentation index](../README.md). For how to **run** the seed and what it
produces, see [development.md](../development.md#dev-data).*

The seed is the fixture set the [`/ui-validation`](../development.md) browser
loop, the `/api-validation` skill and every dev environment run against. That
makes it a shared artefact rather than one developer's scratch data: **a state
the seed never creates is a state nobody can re-check**, and a fixture that
quietly stops meaning what its comment says takes a code path's only coverage
with it. These rules exist because each one has already been broken once.

Everything here is enforced by [`tests/test_seed_coverage.py`](../../tests/test_seed_coverage.py)
unless stated otherwise.

## 1. Naming: purpose for lifecycle fixtures, production shapes for content

Fixtures whose whole job is a lifecycle state are named after that state —
`Checked-In Match`, `On-Site Result Missing`, `Dev Async Qualifier (Closed)`.
Reviews, screenshots and this documentation refer to them by name, and a
developer scanning the schedule needs to see which row is the disputed one.

Everything else carries the content production carries: round-label match titles
(`Winners Round 2 — Match 1`), real preset payloads imported from `presets/`,
filled-in `tournament_format` / rules links / durations. Content that reads like
the real product is what surfaces truncation, wrapping and empty-column bugs.

## 2. Coverage: a row per model, and a row per enum value

Two bars, both mechanical:

- **Every tenant-scoped model** has at least one row for the default tenant.
  Exempt a model in `EXEMPT` with a reason if it genuinely cannot be seeded; the
  dict is currently empty. A *new* model of any kind (global ones too) must also
  be named in some `scripts/seed_*.py`, which the `check_seed_coverage.py` hook
  checks at Stop — a model that cannot be seeded carries a
  `# seed-exempt: Model — reason` comment instead. `McpAuthorizationCode` (global,
  minted mid-flow and consumed seconds later) is the one such entry, in
  `seed_dev.py`.
- **Every value of every enum stored on a model** appears somewhere in the seeded
  database. Enum values are the states the schema itself names, so they are the
  set that can be checked without anyone maintaining a list. `EquipmentStatus.RETIRED`,
  four `SyncStatus` values and three `BotStatus` values were all missing when this
  bar was introduced — every one of them a state the UI renders differently.

Exempt a `Model.field` in `ENUM_EXEMPT` only when seeding the missing values would
be **wrong**, not merely awkward. There are two entries. `DiscordRoleMapping.app_role`:
a mapping per `Role` would describe no real guild, and mapping `SUPER_ADMIN` — a
global role, not grantable per tenant — would be a bug. `PlayerAvailability.status`:
player availability is opt-out, so no surface ever writes an `AVAILABLE` row
(`VolunteerAvailability.status` covers the enum instead).

Derived states that are not enums (match lifecycle, crew fully vs partly staffed,
a roster that is empty, a title that is absent) are not mechanically checkable.
Cover them anyway, and say in a comment what each one is for.

## 3. Identities are derived, never typed

`scripts/seed_support.py` holds the fixture registry. A fixture's `discord_id`
comes from `fixture_discord_id(username)` — a `crc32` of the username inside a
reserved id block — and the test asserts the derived set is unique.

This is not tidiness. `cc_user` and `outsider` once shared a hand-written id, so
the seed created the outsider fixture and then **renamed it**, taking the
membership gate's only fixture with it. Nothing failed; the join page simply
became unreachable in dev. A derived id cannot be typed twice.

`seed_users` re-points an existing row whose username matches but whose id does
not, so a dev database seeded before ids were derived converges instead of
growing a second copy of every fixture.

Other placeholder conventions:

| Kind | Convention |
|---|---|
| Fake URLs | `https://example.invalid/...` — reserved by RFC 2606, never resolves |
| Dev tokens | `wizzrobe_pat_devseed_<tenant>_local_only_do_not_use`; only the hash is stored, exactly like production |
| Guild / Discord role / synthetic event ids | Fixed blocks well outside real snowflake ranges (`1000000000000000001+`, `2000000000000000001+`, `3900000000000000000+`) |
| Racetime / Twitch / Challonge handles | Avoid each mock client's canned identities, so clicking **Link** in dev rebinds rather than colliding |

## 4. Every role has a single-capability holder

For each per-tenant `Role`, one seeded user holds **that role and nothing else**.
`staff_user` satisfies every predicate in the admin area, so a surface that gates
on staff-ness where it means to gate on a capability looks correct in dev until
the delegate it was written for logs in.

`staff_user` therefore holds `STAFF` alone: the equipment and volunteer seeds used
to grant it `EQUIPMENT_MANAGER` and `VOLUNTEER_COORDINATOR`, which bought nothing
(both gates are `is_staff(user) or is_<role>(user)`) and cost the seed its
plain-staff fixture. `SUPER_ADMIN` is exempt — its `UserRole` row carries
`tenant=NULL` and it is the fixture for *no* local grants.

`checkin_desk` holds `CHECK_IN_DESK` alone, so the check-in desk is exercised by
someone who can check people in, link and sync, but not add walk-ups or set up
an event.

## 5. Re-seeding converges; it never overwrites

`get_or_create` for rows. For a field a fixture gains *later*, `backfill()` writes
only where the value is still `NULL` — otherwise the same seed means different
things depending on when your database was made. Nothing overwrites a value a
developer changed by hand.

Two deliberate exceptions, both documented at their call sites: `update_or_create`
for fixtures whose whole point is a specific state something else also writes (the
demo feature-flag groups, which a migration seeds an older version of), and the
explicit `delete()` for fixtures defined by an **absence** — `outsider`'s
memberships — which a create-only seed can never converge.

## 6. States, not volume — with one deliberate exception

The seed's job is one row per meaningful state, and it must stay fast enough to
run **twice** inside the test suite. Pagination, table performance and slow-query
work belong in `scripts/loadtest`.

The exception is `scripts/seed_match_day.py`: fifteen ordinary bracket matches
across the three stages, so the schedule and the stage-utilisation report have a
real day to draw rather than rendering as though nothing is happening. It is a
separate module whose docstring says plainly that its rows cover no new state —
delete it and only density is lost. Density beyond one day does not belong here.

## 7. Tenant B diverges only to exercise a fallback

Everything is seeded for both tenants except where the difference *is* the
fixture for the other half of a branch — tenant B has no station pool (the
free-text fallback), a narrower feature tier, and one randomizer credential
instead of three. Every divergence carries a comment naming the branch it covers.
Anything else is seeded identically, so cross-tenant leak tests compare like with
like.

## 8. One module per domain

`seed_dev.py` orchestrates; each domain's fixtures live in a sibling
`scripts/seed_<domain>.py` called from inside the same `tenant_scope`. This is
also how the files stay under the 800-line budget.

| Module | Fixtures |
|---|---|
| `seed_support.py` | The identity registry, `fixture_discord_id`, `backfill` |
| `seed_matches.py` | The lifecycle matches and everything hanging off them |
| `seed_match_day.py` | Density only (§6) |
| `seed_play_in.py` | Group play-in races — the only rosters longer than two |
| `seed_onsite.py` | The second venue event, and the archived season |
| `seed_payouts.py` | Last season's split paid out in full (with two third places), a pool on the running tournament that nobody has placed in, and the Matcherino handles — one winner deliberately without one |
| `seed_check_in.py` | Event check-in, synced through the real `CheckInService` against the `MOCK_MATCHERINO` roster so pressing Sync in dev changes nothing. One sync yields every automatic link method because three racers carry the identifiers the mock roster matches on (global, via `seed_check_in_identities`): `racer_05` a remembered `matcherino_user_id`, `racer_07` a `twitch_user_id`, `racer_08` a `twitch_username`. On top: a manual link, a withdrawn registrant, a check-in by `checkin_desk`, a member walk-up and a named walk-up, a draft event and a closed one. Only tenants with `EVENT_CHECK_IN` live get rows, which in dev is the default tenant |
| `seed_crew.py` | Commentator/tracker signups — one row per crew state, including the approved-but-unacknowledged one that is the only fixture rendering the Acknowledge control |
| `seed_online.py` | Racetime bots/rooms, presets, SpeedGaming, Discord events |
| `seed_qualifiers.py` | Async qualifiers, open and closed |
| `seed_access.py` | Memberships and per-tenant role grants — including the two fixtures defined by an absence, which is why it is the one module that deletes |
| `seed_venue.py` | Stages, the numbered station pool, and every system-config key |
| `seed_tokens.py` | The deterministic dev bearers and room-screen tokens, each with its revoked/read-only twin |
| `seed_discord.py` | Guild-role mappings, both shapes, plus the grants a sync would leave behind |
| `seed_preferences.py` | Saved table layouts (`UserTablePreference`, global) — one per shape the column reconciler handles, run beside `seed_users` rather than per tenant |
| `seed_branding.py` | A drawn logo for the default tenant only, passed through the real `normalise_logo`, so the header shows both shapes: a logo there, the colour square on `second`. Skipped when the tenant already has one, so a logo uploaded in dev survives a re-seed |
| `seed_events.py` | A community's events beyond its first. Every tenant gets its first event (live, the default) from `EventService.ensure_first_event`; the default tenant also gets SGL 2025 (archived) and SGL 2027 (a draft), one event in each `EventStatus` |
| `seed_brackets.py`, `seed_challonge.py`, `seed_volunteers.py`, `seed_equipment.py`, `seed_observability.py`, `seed_fledgling.py` | Their namesakes |

## What the fixtures deliberately do not do

Recorded so they read as decisions rather than gaps:

- **No team fixtures.** Nothing in the app gives team membership meaning, so a
  fixture would imply semantics that do not exist. `Tournament.team_size` used to
  be stored against that someday and has been dropped; team events happen and are
  tracked outside Wizzrobe — see [current-state.md](../current-state.md).
- **Multi-racer is the exception, not the rule.** Head-to-head is what this app
  runs; the play-in fixture exists because ten-racer group races are the one real
  shape that is not 1v1, and it lives inside the tournament whose bracket it feeds
  rather than in a tournament of its own.
