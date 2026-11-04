# Seed presets and the preset library

A preset is a named randomizer + settings blob that seed generation rolls from. Presets live in **spaces** and are named `space/preset`, the same form SahasrahBot used, so `sgl26/alttpr-open`, `tom/fastboots` and `wizzrobe/open` mean the same preset in every community. No feature flag.

Model detail: [reference/data-model.md](../reference/data-model.md#presetspace). Randomizer backends and per-community keys: [reference/seed-generation.md](../reference/seed-generation.md).

## Spaces

| Kind | Slug | Who edits | Notes |
|---|---|---|---|
| `community` | the community's slug, one per community | STAFF and PRESET_MANAGER (`can_manage_presets`) | Admin → Seed Presets. Presets keep their `tenant`, so every tournament path stays tenant-scoped |
| `user` | from the player's Discord name, made on first save; renamable | owner + up to 10 co-editors (`PresetSpaceEditor`) | Co-editors edit presets but can't rename the space or change editors. 50 presets per space, 30 writes per 10 minutes |
| `builtin` | `wizzrobe` (reserved) | nobody | Every file under `presets/` (Wizzrobe's own plus SahasrahBot's globals for ALTTPR, ALTTPR mystery, Door Randomizer, SM, SMZ3). `BuiltinPresetService.ensure_synced` brings the rows in line with the files on the first library read per process; rows whose file is gone are deleted and copies keep their settings |

Names and personal slugs are `[a-z0-9_-]`, 2–32 chars. Settings and weightsets are capped at 64 KB; descriptions are player markdown (text and links only).

## The library

`PresetLibraryService` is the one place that reads across spaces, on purpose. Anyone signed in (a community member) can browse, read and roll:

- every **listed** community preset (`Preset.listed`; presets that existed before the library started hidden, new ones start shown), plus their own community's unlisted ones if they're its staff, flagged;
- every personal preset (personal spaces are always public);
- the `wizzrobe` space.

Signed-out visitors get nothing. Rolls use the **roller's current community's** randomizer keys, never the owner's; a game with no key there shows why Roll is off. A player gets 10 rolls per 10 minutes (STAFF and PRESET_MANAGER of the community are unlimited), and every roll bumps `roll_count`, which the library shows and sorts by.

**Copy to use.** A tournament only points at a preset in its own community (leak-tested). Staff copy a library preset in (`copy_to_community`); players copy into their own space (`copy_to_space`). The copy records `copied_from` + `copied_at`; when the original changes, the copy shows "Changed since you copied" with a diff of the changed keys, and **Update copy** (`update_copy`) is the only way it changes.

**Import from SahasrahBot.** In the player's space settings, a SahasrahBot namespace lists its presets (`application/utils/clients/sahasrahbot_client.py`, SahasrahBot's public preset API for `alttpr`, `alttprmystery`, `sm`, `smz3`); ticked ones (25 per call) copy into the player's space. Door presets land as `alttprdr`, AVIANART ones as `avianart`; presets using `forced_locations` are refused.

## ALTTPR mystery weightsets

A mystery preset is the randomizer `alttprmystery` (no `kind` column), so tournaments, seed rules and every per-randomizer table take it like any other. Its settings are a weightset in SahasrahBot's full format: weights per setting, `subweights`, `customizer` (eq, custom, timed-ohko…), `rules`, `custom_instructions`, `preset:` picks of built-in presets, and door settings that roll on the Door Randomizer.

- The roller is `application/services/_seedgen_alttprmystery.py`, a port of pyz3r's `generate_random_settings` plus SahasrahBot's `mysterydoors`, rolled through the `MysteryBackend` mixin on `SeedGenerationService`.
- Saving one parses the YAML with no aliases, caps nesting at 12 and subweight steps at 32, stringifies keys, checks every `preset:` name and dry-rolls it 5 times. `POST /api/preset-library/mystery/check` returns the odds per setting for the editor.
- A rolled seed stores `{'mystery': {weightset, preset, instructions, doors}, 'settings': <rolled payload>}`. `SeedGenerationService.public_settings` hides the payload from REST match reads (players see the weightset name and the instructions); `seed_instructions` puts the instructions in the seed DM, the library roll and the racetime post.

## Rolling in racetime rooms

`!roll space/preset` in any room the bot is in rolls a listed library preset through `PresetLibraryService.roll_reference` and posts `<space/preset> for <name>: <url> · Code: … · <instructions>`. A bare `!roll preset` rolls from the room's community space. In a match room the bot answers only when it runs the room (`racetime_bot_runs_room`). The roller needs a linked racetime account (`User.racetime_user_id`); the same visibility, keys and 10-per-10-minutes limit apply as in the app. A name that matches presets for two games is refused with a pointer to the app. Command table: [online-tournaments.md](online-tournaments.md).

## Surfaces

- New app: Me → Presets (the library in a side list: Yours · community · Everything, game chips, search; phone opens the preset full screen) and Admin → Seed Presets (the community's own space, with Browse library).
- REST: `/api/preset-library` (list, `/randomizers`, `/mystery/check`, `/spaces`, `/spaces/{id}`, `/spaces/{id}/editors`, `/members`, `/personal`, `/sahasrahbot/{namespace}` + `/import`, `/{id}`, `/{id}/roll`, `/{id}/copy`, `/{id}/copy-to-mine`, `/{id}/update-copy`). See [reference/rest-api.md](../reference/rest-api.md).
- MCP: `list_library_presets`, `get_library_preset` (read only; a mystery preset comes back with its odds).
- Audit: `preset_space.*` and `preset.copied`. No bus events (no outside consumer).

## Out of scope

Discord slash commands, mystery weights for games other than ALTTPR, live-linked presets (a tournament following someone else's preset) and private personal presets.
