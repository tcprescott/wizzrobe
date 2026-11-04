# Group race rooms

Status: Building · Size: full · PRs: #817 (backend for dailies, merged), PR 2 (qualifier live races) · Requested: 2026-11-03, Thomas

## Problem

SahasrahBot opens racetime rooms for group races: the SpeedGaming daily and
weekly races (ALTTPR daily, SMZ3 weekly) and the ALTTPR main tournament's live
qualifiers. An hour before each SG episode it opens an open (not invite-only)
room, posts the link with a role ping in a Discord channel, and posts the seed
10 minutes before the start; for qualifiers it also locks the room so nobody
joins late. Wizzrobe can't do any of that yet. Auto-open skips a match with no
players, the only notice is a DM to each player, the bot holds any result with
entrants it doesn't know, and qualifier live races have no start time, so staff
open each room by hand and the bot never posts their seed or locks them.

## Scope

In:
- **One group race mode** for a tournament (dailies/weeklies) and for an async
  qualifier's live races. A group race has a start time and no player list;
  anyone can join its room.
- **Where races come from**: the SpeedGaming sync (a group race tournament's
  episodes become group races, as SahasrahBot does), plus **New group race** in
  the app for staff (title + start time).
- **Room opens on its own** `room_open_minutes_before` the start (default 60 for
  group races), open to all, through the tournament's (or qualifier's) racetime
  bot and race room profile. "co-op" in an SG episode title makes it a team
  race, as in SahasrahBot.
- **Discord announcement**: each group race tournament or qualifier names a
  channel and an optional role. When the room opens the bot posts the title,
  start time (Discord `<t:…>` markup), seed time and a **Join the race room**
  link button there, pinging the role.
- **Bot runs the room**, reusing PR #725: greets, `!seed`, `!fpa`, posts the
  seed `racetime_seed_post_minutes` before the start (default 10). Dailies roll
  through the community presets `space/preset` lookup (no seed rules of our
  own). Qualifier live races post their pool permalink instead of rolling.
- **Lock at seed time**: a per-tournament / per-qualifier switch, *Lock the
  room when the seed posts*. Off by default for tournaments, on by default for
  qualifiers. When on, the room turns invite-only as the seed posts and the bot
  says "This room is now locked. Late entries are not permitted."
- **Results**: a daily's finish records the finish order on the match. Linked
  entrants are added as match players with rank and time; unlinked entrants are
  listed by racetime name on the match. Nothing is held for staff (the held
  result checks for strangers don't apply). Qualifier live races keep today's
  capture (runs, par, unmatched handles).
- **Group races screen** in the new app (layout per the mockup pick) where
  staff see upcoming and recent group races, open a room early, re-post the
  announcement, cancel, and add a race by hand.
- **Tournament → Racetime tab** in the new app with the existing room-bot
  fields plus the group race ones; the qualifier form gets its group race
  fields. Both also on REST.
- Update `docs/features/online-tournaments.md`: community dailies move from
  "retires" into the succession scope.

Out:
- Seed rules (thread "Design seed rolling rules for tournaments") and preset
  naming (community presets thread). We call their lookup.
- KONOT elimination chains.
- A repeating schedule inside Wizzrobe (SG schedule or hand-added only).
- An SG webhook post (SahasrahBot's second announcement for the ALTTPR daily).
- The old NiceGUI app (new app only).
- Player-facing pages beyond what exists (the room already shows on the
  Event tab and live page as an open room).

## Flow

Staff (once per series):
1. Admin → Tournaments → the daily's tournament → **Racetime** tab. Pick the bot
   and profile, choose **Group race (anyone can join)**, set the announcement
   channel and role, room-open and seed-post minutes, the preset, and *Lock the
   room when the seed posts*. Save.
2. Link the SG event under Admin → SpeedGaming as today. Or add a race by hand
   from **Group races › New group race**.

The bot, per race:
1. At start − room-open minutes: open the room (open to all), post the Discord
   announcement with the role ping and a **Join the race room** button.
2. At start − seed-post minutes: roll (or take the permalink), post it in the
   room, lock the room if the switch is on.
3. Race finishes: record the finish order (daily) or capture runs (qualifier),
   close the room.

Player:
1. Sees the ping in the channel, taps **Join the race room**, joins on racetime.
2. Gets the seed in the room; races. A linked player sees the race on their
   history afterwards.

Staff, on the day: **Group races** shows each race's state (opens at / room
open · N entrants / seed posted · locked / running / finished). Row ⋮: Open
room now, Re-post announcement, Cancel race.

## Mockup pick

**A**: a new Admin → Group races screen, a side list of races by day beside the open race (bot steps, entrants, Discord post; Actions: post seed now, re-post, lock now, cancel). Phone: list, then the race full screen. Settings live on the Tournament → Racetime tab (**S**). Images:
`/mnt/project-files/group-race-rooms/mockups/{A,S}-{phone,desktop}-{light,dark}.png`.

## Data and permissions

- **Feature flag:** reuse `RACETIME_ROOMS` (no new flag). The worker, the Group races screen and REST check it; the owning services already carry `@requires_feature(FeatureFlag.RACETIME_ROOMS)`. Qualifier live races keep `ASYNC_QUALIFIERS` and also need `RACETIME_ROOMS` for auto-open.
- **Models:** `Tournament` gains `racetime_group_race` (bool, worker-scanned),
  `racetime_lock_at_seed` (bool), `racetime_announce_channel_id` and
  `racetime_announce_role_id` (typed columns, digits only, so the worker can
  filter on them). `AsyncQualifier` gains `live_race_*` group race fields
  (bot, channel, role, lock, room-open and seed-post minutes; no profile, since
  live race rooms open with racetime's defaults today);
  `AsyncQualifierLiveRace` gains `scheduled_at` (the post and lock stamps live
  on the room, as for dailies).
  `RacetimeRoom` gains `announced_at`, `locked_at` and `unmatched_entrants`
  (JSON, racetime names + rank/time; on the room, since it's what racetime
  reported). Tournaments and
  matches stay event-owned; qualifiers stay community-owned. One migration.
- **Who can see / do what:** STAFF (and tournament admins for their tournament)
  edit group race settings and run the Group races screen; qualifier admins do
  the same for their qualifier's live races. Players do nothing new in the app.
- **Other surfaces:** REST for the new tournament/qualifier fields and the
  Group races list/actions; the existing `race_room_worker` drives open + seed
  for both kinds (no new worker). Discord channel post (not a DM, so no
  notification-catalogue entry; the post is audited rather than logged in
  Sent messages, which only holds DMs). Audit
  `race_room.announced`, `race_room.locked`, `group_race.created`,
  `group_race.cancelled`; matching `EventType`s. MCP: `list_group_races` read
  tool.
- **Player input:** none new. Racetime names from unlinked entrants are stored
  as plain text and rendered escaped.

## PR plan

1. Spec + backend: model fields + migration, open rooms with no players for
   group race tournaments, open-to-all room, Discord announcement, lock at seed,
   finish order recording, SG sync for group race tournaments, REST for the
   tournament fields, seed rows, doc update. Covers acceptance 1–6, 9. No
   changelog line yet: staff can't switch it on in the app until PR 3.
2. Qualifier live races as group races: `scheduled_at`, auto-open, bot runs the
   room with the permalink, lock, announcement. Covers 7.
3. New app, split in two:
   - 3a: Tournament → Racetime tab (layout S), the qualifier's live race
     settings, `GET /api/group-races/options` (bots, profiles, Discord
     channels and roles), changelog. Covers 8 and 10.
   - 3b: Group races screen (layout A) with its REST list/actions, adding a
     race by hand, the `list_group_races` MCP tool, help article. Covers 11–12.

## Acceptance

- [ ] 1. A group race tournament's match with no players gets a room
      `room_open_minutes_before` its start, opened as not invite-only.
- [ ] 2. When that room opens, one message lands in the tournament's Discord
      channel with the role ping, the start time in `<t:…>` markup and a
      **Join the race room** link button; a bot restart doesn't post it twice.
- [ ] 3. The bot posts the seed (rolled through the presets lookup)
      `racetime_seed_post_minutes` before the start.
- [ ] 4. With *Lock the room when the seed posts* on, the room turns invite-only
      as the seed posts and the bot says so; with it off, it stays open.
- [ ] 5. On finish, linked entrants become match players with rank and time,
      unlinked ones are listed by racetime name, and nothing is held.
- [ ] 6. An SG episode for a group race tournament becomes a group race with no
      players; "co-op" in its title opens a team race.
- [ ] 7. A qualifier live race with a start time opens on its own, posts its
      permalink at seed time, locks (default on), and captures runs as today.
- [ ] 8. Staff can add a group race by hand from the Group races screen.
- [ ] 9. Head-to-head tournaments behave exactly as before (existing room bot
      tests pass unchanged).
- [ ] 10. On a phone, the Group races screen and the Racetime tab fit at 360px
      with no sideways scroll, in light and dark.
- [ ] 11. On desktop, the Group races screen shows the list beside the open
      race (per the picked layout).
- [ ] 12. The new pages pass the a11y scan in light and dark.

## Open questions


## Decisions

- 2026-11-04 — Thomas picked **dailies + qualifier live races**: one open-race
  mode covers both.
- 2026-11-04 — Thomas picked **SG schedule + add in app** for where a race's
  start time comes from.
- 2026-11-04 — Thomas picked **channel post + role ping** for how players hear
  a room is open.
- 2026-11-04 — Thomas picked a **per-tournament switch** for locking the room
  at seed time (off for dailies, on for qualifiers by default).
- 2026-11-04 — Thomas picked **finish order on the match** for daily results:
  linked entrants become players with times, unlinked ones listed by name,
  nothing held.
- 2026-11-04 — Thomas picked **reuse the Racetime rooms flag**: no new flag.
- 2026-11-04 — Thomas picked mockup **A**: the Group races side-list screen. Spec approved.
- 2026-11-04 — Seeds roll through the community presets thread's
  `space/preset` lookup (coordinator note); no group-race seed logic.
