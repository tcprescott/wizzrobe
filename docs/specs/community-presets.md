# Community presets

Status: Building · Size: full · PRs: #816 (PR 1, merged), #819 (PR 2, merged), PR 3 built-ins + SahasrahBot import · Requested: 2026-11-03, Thomas

## Problem

Seed presets in Wizzrobe belong to one community and only its staff and preset
managers can touch them. Players can't keep their own presets, nobody can see
or reuse another community's, ALTTPR mystery weightsets don't exist, and the
only way to roll a seed is through a tournament match. SahasrahBot covered all
of that (a preset space per player with co-editors, `space/preset` names anyone
could roll, ~170 global presets, mystery weightsets, `/alttpr preset` from
Discord), so players coming from it lose things they used weekly.

## Scope

In:
- **Preset spaces.** Every preset lives in a space with a short, unique slug.
  Each community has one (its slug, edited by STAFF and PRESET_MANAGER as
  today). Every player gets a personal space the first time they save a preset
  (slug from their Discord name, changeable), and can add **co-editors**.
- **Public library.** Anyone signed in can browse and search every preset in
  every space, filter by game, and open one to read its settings. A preset's
  full name is `space/preset`, the same form SahasrahBot used.
- **Show in the library**: a
  switch per community preset. Presets that existed before this ships start
  hidden, because today their settings are staff-only on purpose (race-day
  secrets); new ones start shown. Staff of the owning community still see
  hidden ones, flagged.
- **Copy to use.** A tournament only points at a preset in its own community's
  space. Staff copy a library preset in (**Copy to <community>**); the copy
  remembers where it came from, and when the original changes the copy shows
  "Changed since you copied" with **Update copy** (never automatic). Players
  can copy any preset into their own space the same way.
- **Built-in space**. SahasrahBot's global presets (ALTTPR,
  ALTTPR mystery, SM, SMZ3) as one read-only `wizzrobe` space. Door Randomizer
  presets appear once PR #474 lands. Replaces **Import built-in presets**.
- **ALTTPR mystery weightsets**, SahasrahBot's full YAML format: weights,
  `subweights`, `customizer` (eq/custom/timed-ohko…), `rules`,
  `custom_instructions`. A preset is either *settings* or *mystery*; only
  ALTTPR offers mystery. The editor shows the odds per setting beside the YAML.
  A mystery roll keeps the rolled settings hidden from players (staff and
  proctors see them on the seed); players see the custom instructions.
  Tournaments and seed rules can point at a mystery preset like any other.
- **Roll a seed** from any preset in the app (signed in), and in racetime rooms
  the bot runs: `!roll space/preset` (and `!roll preset` for the room's own
  community). The result shows the seed link and code, and the roll count goes
  up. Rolls use the roller's current community's randomizer keys, never the
  owner's; a game that
  community has no key for shows why Roll is off.
- **Import from SahasrahBot**: in My presets, type a SahasrahBot
  namespace, tick presets, they copy into your space.
- Roll counts per preset ("rolled 214 times"), shown in the library and used to
  sort it.
- New app only (`web/`), REST for the app, MCP read tools.

Out:
- Discord slash commands (Thomas, 2026-11-03).
- Mystery weights for games other than ALTTPR.
- Live-linked presets (a tournament following someone else's preset).
- Private personal presets: everything in a player's space is public.
- Choosing which preset a match rolls; that's the seed rolling rules thread.
- Spoiler races and opening group race rooms (sibling threads); they'll call
  this thread's `space/preset` lookup.

## Flow

Player:
1. Me → **My presets** (or Home → Presets) opens the library on *Yours*.
2. **New preset** → game → name → settings (paste box or JSON, as the admin
   editor works today) or, for ALTTPR, **Mystery weightset** (YAML + odds).
   Save. It's now `tom/fastboots`.
3. Browse: switch to *Everything*, filter by game, search. Open
   `sgl26/alttpr-open` → **Roll a seed** (link + code) or **Copy to my presets**.
4. Space settings (⋮ on the space header): rename slug, add or remove
   co-editors by member search, **Import from SahasrahBot…**.
5. In a racetime room the bot runs: `!roll tom/fastboots`.

Staff (STAFF, PRESET_MANAGER):
1. Admin → Seed Presets is the community's own space, same screen as today, plus
   a **Browse library** action.
2. In the library, any preset → **Copy to SpeedGaming Live** → it lands in the
   community list, ready to attach to a tournament.
3. A copied preset whose original changed shows a banner with **Update copy**
   and a diff of the changed keys.

Proctor: no change; the seed card on a mystery match shows "Mystery: friendly"
and the rolled settings.

## Mockup pick

A, one library in a side list (Thomas, 2026-11-04): every preset in one list
grouped Yours / this community / everyone else, with a Yours · community ·
Everything switch, game chips and search; the open preset sits beside it with
Roll a seed and Actions. Phone: list, then the preset full screen. M (mystery
editor, YAML + odds) and S (space settings, co-editors, SahasrahBot import) as
drawn. Images:
`/mnt/project-files/community-presets/mockups/<option>-{phone,desktop}-{light,dark}.png`.

## Data and permissions

- **Feature flag:** no (Thomas, 2026-11-04).
- **Models:** new `PresetSpace` (global: `slug` unique, `kind`
  community/user/builtin, `tenant` FK for community spaces, `owner` FK User for
  personal ones); `PresetSpaceEditor` (space, user). `Preset` gains `space` FK,
  `kind` (settings/mystery), `copied_from` (preset FK, null on delete) +
  `copied_at`, `roll_count`; `tenant` becomes null for personal and built-in
  presets; unique becomes (space, randomizer, name). Migration creates one
  space per community and moves its presets in. Community presets stay
  tenant-scoped for every tournament path; the library reads across spaces on
  purpose through one service (`PresetLibraryService`), leak-tested so a
  tournament can never attach another community's preset.
- **Who can see / do what:** browse, read and roll = any signed-in user. Edit a
  personal space = owner + co-editors. Edit a community space = STAFF and
  PRESET_MANAGER of that community (today's rule). Built-in space = nobody
  (repo files). Copy into a community = that community's STAFF/PRESET_MANAGER.
  Signed-out visitors get nothing.
- **Other surfaces:** REST `/api/preset-library/*` (spaces, presets, roll,
  copy, editors, sahasrahbot import); MCP read `list_library_presets`,
  `get_library_preset`; racetime `!roll`. Audit `preset_space.*` and
  `preset.copied`; events not needed (no outside consumer).
- **Player input:** names and slugs are `[a-z0-9_-]`, 2–32 chars; descriptions
  are player markdown (text + links). Settings/YAML capped at 64 KB, parsed with
  `yaml.safe_load`, validated against the randomizer before save. Player rolls
  limited to 10 per 10 minutes per person (staff unlimited); `!roll` shares the
  limit; 50 personal presets per space; 10 co-editors.

## PR plan

1. Spec + `PresetSpace` model and migration (community spaces, existing presets
   moved), library service + REST reads, copy-with-provenance, leak tests,
   seed rows. Library screen (picked layout) with read, copy, roll.
2. Personal spaces: create on first save, editor screen for personal presets,
   co-editors, slug rename, rate limits, help article, changelog.
3. Built-in `wizzrobe` space from SahasrahBot's preset files (ALTTPR, SM,
   SMZ3), retire Import built-ins; Import from SahasrahBot.
4. ALTTPR mystery: weightset parser/roller (port with tests against
   SahasrahBot's files), editor with odds, hidden-settings display, tournament
   and rule support.
5. racetime `!roll space/preset` + MCP read tools; spec deleted, docs folded
   into `docs/features/seed-presets.md`.

## Acceptance

- [ ] Every existing community preset is in its community's space and every
      tournament still rolls exactly what it rolled before.
- [ ] A signed-in player can find `sgl26/<preset>` from another community and
      roll it; a signed-out visitor can't open the library.
- [ ] A tournament can't be pointed at a preset outside its community, even by
      REST (leak test).
- [ ] Copying records the original; editing the original shows "Changed since
      you copied" on the copy; Update copy is the only way the copy changes.
- [ ] A co-editor can edit, can't delete the space or change co-editors.
- [ ] A mystery roll of SahasrahBot's `friendly.yaml` gives settings within its
      weights (test over many rolls) and hides them from the player.
- [ ] `!roll tom/fastboots` in a bot room posts a seed; an 11th roll inside 10
      minutes is refused politely.
- [ ] On a phone, the library list opens a preset full screen and back returns
      to the list.
- [ ] On desktop, the list and the open preset sit side by side.

## Open questions


## Decisions

- 2026-11-04 — Thomas picked communities + every player own presets (personal spaces like SahasrahBot namespaces).
- 2026-11-04 — Thomas picked a public library where you copy a preset to use it.
- 2026-11-04 — Thomas picked ALTTPR mystery in SahasrahBot's full format.
- 2026-11-04 — Thomas picked rolling from an app page and racetime rooms (no Discord commands).
- 2026-11-04 — Thomas picked no feature flag.
- 2026-11-04 — Thomas picked a read-only built-in `wizzrobe` space holding SahasrahBot's global presets; Import built-ins goes away.
- 2026-11-04 — Thomas picked Import from SahasrahBot by namespace (copies into the player's space via SahasrahBot's public preset API).
- 2026-11-04 — Thomas picked layout A, one library in a side list.
- 2026-11-04 — Thomas picked a Show in library switch per community preset (existing ones start hidden, new ones shown).
- 2026-11-04 — Thomas picked rolls using the roller's current community's randomizer keys.
