# Seed Generation Reference

*Implementation reference for randomizer seed generation: `SeedGenerationService`, the per-randomizer generators, and the settings presets under `presets/`. Part of the [documentation index](../README.md).*

## Overview

Tournaments for randomized games need a freshly rolled game ("seed") for every match. A [`Tournament`](../../models/tournament.py) picks a [`Preset`](#presets-db-backed) (the `preset` FK — a tenant-authored randomizer + settings blob), and the preset names the randomizer: `TournamentService` writes `Tournament.seed_generator` from `preset.randomizer` on every create/update, and clears it when the preset is detached. The nullable `seed_generator` column stays as the resolved randomizer that the roll button, the triforce-text gate and the REST payloads read; only a REST caller that names one explicitly alongside `preset_id=None` still sets it by hand. Staff roll a seed per match from the admin schedule, and the resulting URL is stored and shown to players, crew, and API consumers.

| File | Contents |
|---|---|
| [`application/services/seedgen_service.py`](../../application/services/seedgen_service.py) | `SeedGenerationService`: `AVAILABLE_RANDOMIZERS`, `generate_seed()` dispatch, per-randomizer generators |
| [`application/services/match/match_schedule_service.py`](../../application/services/match/match_schedule_service.py) | `MatchScheduleService.generate_seed()` — the production entry point: locking, validation, persistence, DMs, audit |
| [`application/services/preset_service.py`](../../application/services/preset_service.py) | `PresetService`: CRUD (gated by `AuthService.can_manage_presets`), `import_builtins` from the `presets/` files, and `import_remote_presets` from a randomizer's own API |
| [`application/services/_seedgen_dk64r.py`](../../application/services/_seedgen_dk64r.py) | `DK64RBackend`: every DK64R call — settings conversion, submit/poll, and the published preset catalogue |
| [`application/services/_seedgen_avianart.py`](../../application/services/_seedgen_avianart.py) | AVIANART: generate, poll until built, and the published preset catalogue |
| [`application/services/_seedgen_samus.py`](../../application/services/_seedgen_samus.py) | samus.link: Super Metroid (`sm.samus.link`) and SMZ3 (`samus.link`) rolls |
| [`application/services/_seedgen_varia.py`](../../application/services/_seedgen_varia.py) | Super Metroid VARIA: settings + skills presets, race roll on randommetroidsolver.pythonanywhere.com |
| [`application/services/_seedgen_types.py`](../../application/services/_seedgen_types.py) | `RolledSeed`, `RemotePreset`, `AsyncRollSubmission`, `AsyncRollPoll` |
| [`presets/`](../../presets) | Built-in settings files (`alttpr/`, `dk64r/`, `ff1r/`, `ootr/`, `sm/`, `smdash/`, `smmap/`, `smvaria/`, `smz3/`, `wwr/`, `z1r/`) — starting rows imported into the `Preset` table |
| [`models/tournament.py`](../../models/tournament.py), [`models/match.py`](../../models/match.py) | `Tournament.seed_generator`, `Tournament.preset`, `Tournament.hard_preset`, `Preset`, `GeneratedSeeds`, `ProviderTask`; `Match.generated_seed`, `Match.preset_override` |
| [`theme/dialog/tournament_edit_dialog.py`](../../theme/dialog/tournament_edit_dialog.py) | The Seed Preset select on the tournament create/edit dialog |
| [`pages/admin_tabs/admin_presets.py`](../../pages/admin_tabs/admin_presets.py) | Admin **Presets** tab: preset CRUD, import built-ins, and a per-row **Test roll** |
| [`theme/tables/match_slots.py`](../../theme/tables/match_slots.py), [`theme/tables/match.py`](../../theme/tables/match.py), [`theme/tables/match_lifecycle.py`](../../theme/tables/match_lifecycle.py) | The per-row **Generate** button (`SEED_ROLLABLE` gate), its `roll` event, and the `on_generate_seed` handler shared by the admin Schedule tab and the proctor board |
| [`application/randomizer_credentials.py`](../../application/randomizer_credentials.py) | `CredentialSpec` registry: which credential each keyed randomizer needs |
| [`application/services/randomizer_credential_service.py`](../../application/services/randomizer_credential_service.py) | `RandomizerCredentialService`: per-tenant credential CRUD + roll-time resolution |
| [`pages/admin_tabs/admin_randomizer_keys.py`](../../pages/admin_tabs/admin_randomizer_keys.py) | Admin **Randomizer Keys** tab: enter/clear this community's credentials |

### Selecting a randomizer per tournament

The tournament create/edit dialog ([`tournament_edit_dialog.py`](../../theme/dialog/tournament_edit_dialog.py)) renders one select:

- **Seed Preset** — options are the tenant's `Preset` rows (loaded via `PresetService.list_selectable`), plus a `— None (no seeds) —` entry (id `0`) that maps back to `NULL`. `TournamentService.create_tournament`/`update_tournament` take a `preset_id`; it is validated against the tenant-scoped `PresetRepository` (a preset from another tenant is rejected), and on update `None` clears the FK while omitting the argument leaves it untouched. Attaching a preset writes `seed_generator = preset.randomizer`; clearing one clears `seed_generator` too, unless the same call named a generator explicitly.

A tournament configured before the preset became the only control keeps rolling on its stored `seed_generator`; the dialog says so under the select, and saving it with no preset is what turns seed rolling off. Migration `61_20260803060000_preset_owns_randomizer` backfilled `seed_generator` for the tournaments that had a preset but no generator.

The admin Tournaments tab lists each tournament's `seed_generator` read-only.

### Generation flow

1. The admin Schedule tab's match table (and the proctor board) shows a **Generate** button (casino icon) in the Seed column for rows where `tournament_seed_generator` is set, the match is not a racetime match, it has players, it is not Finished/Confirmed, no seed exists yet and no roll is in flight (`SEED_ROLLABLE` in [`match_slots.py`](../../theme/tables/match_slots.py); the card/mobile layout has the same button). Clicking emits a `roll` event that ends in `MatchLifecycleHandlers.on_generate_seed` in [`match_lifecycle.py`](../../theme/tables/match_lifecycle.py).
2. `on_generate_seed` calls `MatchScheduleService.generate_seed(match_id, actor=...)`, which validates permission and state (see [API](#seedgenerationservice-api) below), then resolves the preset through `MatchHardPresetService.resolve_preset(match)` and takes the randomizer from it (falling back to the legacy `tournament.seed_generator` string with no preset), and dispatches `SeedGenerationService.generate_seed_call(randomizer, preset, surface='match', triforce_text=…)` (an approved community text for an ALTTPR roll — see [below](#alttpr-tournament-generation-and-triforce-texts)) (or, for an `ASYNC_RANDOMIZERS` backend, queues a `ProviderTask` — see [Asynchronous rolls](#asynchronous-rolls)).

   **Which preset that is, is the players' answer as much as the tournament's.** In order: staff's per-match `Match.preset_override`; else `tournament.hard_preset` when every player opted into it; else `tournament.preset`. This is the only place the question is asked, and asking it here is what closes the opt-in window — from that line on, the settings are what `GeneratedSeeds` records rather than what anyone picks. See [match-participation.md](../features/match-participation.md#harder-settings-opt-in).
3. The returned string is persisted as a [`GeneratedSeeds`](../../models/tournament.py) row and linked from the match:

   | `GeneratedSeeds` field | Value |
   |---|---|
   | `tenant` | required FK (`on_delete=CASCADE`, `related_name='generated_seeds'`) |
   | `seed_url` | the generator's return value (a URL for most randomizers; a plain string for Z1R) |
   | `seed_info` | `"Generated seed for match {id}"` |
   | `randomizer` | the backend that actually rolled it |
   | `preset` | nullable FK to the `Preset` that won resolution (`SET_NULL`) |
   | `settings_snapshot` | the settings **as sent upstream** (`RolledSeed.settings`), never updated — survives later preset edits; no credential is ever included |
   | `rolled_by` | nullable FK to the acting `User` (`SET_NULL`) |
   | `provider_meta` | `ProviderCall.as_meta()` — attempts, latency, surface |
   | `created_at` / `updated_at` | automatic timestamps |

   `Match.generated_seed` is a nullable FK to `GeneratedSeeds` (`related_name='matches'`). Persistence, DMs and audit all live in `MatchScheduleService.complete_seed_roll`, the one completion path shared by synchronous rolls and the [task-queue worker](#asynchronous-rolls).
4. Players are DM'd the seed URL in the background via the Discord queue (`send_dm` honours each user's Discord switch, `dm_notifications`), and an audit entry `match.seed_rolled` (`AuditActions.MATCH_SEED_ROLLED`) is written with `match_id`, `randomizer`, `preset` (the preset name, or `None` when rolled from the legacy `seed_generator`), and `seed_url`.

The seed is displayed on the home Schedule and Player tabs and in the admin match table — values matching `^https?://` render as truncated hyperlinks, anything else as plain text. The REST API exposes it as `MatchResponse.generated_seed` (`GeneratedSeedBase`: `id`, `seed_url`, `seed_info`, `randomizer`, `settings_snapshot`, `provider_meta`, `created_at`) in the [`api/`](../../api/) package ([rest-api.md](rest-api.md)). The admin match dialog's **Clear Seed** button ([`match_dialog.py`](../../theme/dialog/match_dialog.py)) sets the FK back to `NULL` via `MatchService.update_match(clear_seed=True)`; the `GeneratedSeeds` row itself is not deleted.

## SeedGenerationService API

`SeedGenerationService` ([`seedgen_service.py`](../../application/services/seedgen_service.py)) is stateless — instantiate it freely; `MatchScheduleService` creates one in its constructor. See [services.md](services.md) for the surrounding service layer.

```python
AVAILABLE_RANDOMIZERS = ['alttpr', 'avianart', 'ff1r', 'z1r', 'z2r', 'smb3r', 'aosr', 'sm', 'smz3',
                         'smmap', 'ootr', 'smdash', 'smvaria', 'dk64r', 'wwr', 'test']
STUB_RANDOMIZERS = set()                                # registered but not wired (none today)
PRESET_AWARE_RANDOMIZERS = everything but 'ootr', 'smmap', 'test'  # use preset.settings when given
MOCK_EXEMPT_RANDOMIZERS = {'aosr', 'dk64r', 'smb3r', 'wwr', 'z1r', 'z2r'}  # MOCK_SEEDGEN leaves these alone
ASYNC_RANDOMIZERS = {'dk64r'}                           # task-queue backends (submit now, collect later)
TRIFORCE_TEXT_RANDOMIZERS = {'alttpr'}                  # can embed community triforce texts
REMOTE_PRESET_BRANCHES = {'avianart': (), 'dk64r': ('stable', 'dev')}   # upstream preset catalogues
PROVIDER_TIMEOUTS = {'avianart': 150.0, 'dk64r': 660.0, 'smvaria': 100.0}
PROVIDER_ATTEMPTS = {'avianart': 1, 'dk64r': 1, 'smdash': 1, 'smvaria': 1}
```

`AVAILABLE_RANDOMIZERS` is the full validity set — it drives `MatchScheduleService`'s validity check and stays whole regardless of what any tenant has configured. What a tenant may *select* is the narrower `SeedGenerationService.available_randomizers(configured)`, which drops randomizers whose credential this community has not supplied; the tournament dialog, the Presets tab, and the REST `/seeds/randomizers` catalogue all render that filtered list. See [Per-tenant credentials](#per-tenant-credentials).

### Stub randomizers

`STUB_RANDOMIZERS` is the mechanism for a randomizer that is selectable on tournaments and appears in every UI/API surface reading `AVAILABLE_RANDOMIZERS`, but whose generator raises `ValueError` (the documented user-error contract) because it isn't wired yet. It is empty today: `dk64r`, `smdash` and `wwr` were all promoted out of it.

`MOCK_SEEDGEN` returns a fake `mock.seedgen.local` URL for every randomizer except `MOCK_EXEMPT_RANDOMIZERS`. `dk64r` mocks one layer down (see [dk64r](#dk64r)); `wwr`, `z1r`, `z2r`, `smb3r` and `aosr` are built locally with no upstream to stand in for, and most of their seeds are plain strings, which is what dev needs to show the copy card ([Pasted seeds](#pasted-seeds-the-copy-card)).

### Per-tenant credentials

A randomizer whose upstream is key-gated needs a credential, and that credential belongs to the **community**, not the deployment: each one supplies the key it was issued and is bound by the terms attached to it. This replaces the former process-wide `*_API_KEY` environment variables; there is no deployment-wide fallback.

Which credentials exist is declared once in [`application/randomizer_credentials.py`](../../application/randomizer_credentials.py), so the admin form and the availability check stay generic:

```python
@dataclass(frozen=True)
class CredentialSpec:
    randomizer: str   # 'ootr'
    key: str          # stable key persisted on RandomizerCredential.key
    label: str        # 'OoT Randomizer API key'
    help_text: str    # what it is / where a community obtains one
```

Values live in `RandomizerCredential` (`tenant`, `randomizer`, `key`, `value`), unique on `(tenant, randomizer, key)`. `value` is **plaintext at rest and guarded at the service layer** — the same contract as `RacetimeBot.client_secret`, `ChallongeConnection.access_token`, and `Webhook.secret`: `RandomizerCredentialService.list_status` reports configured-or-not and never the value, the audit entries (`randomizer_credential.set` / `.cleared`) name the credential and nothing else, and `resolve()` — the one unmasked read — is called only by `SeedGenerationService` mid-roll. Community STAFF/`PRESET_MANAGER` manage them on the admin **Randomizer Keys** tab (gated by `AuthService.can_manage_presets`); the input is write-only and a stored value is never rendered back.

The credential acts at two kinds of surface:

- **Selection** — `available_randomizers(configured)` filters the tournament dialog, the Presets tab randomizer select, and the REST `/seeds/randomizers` catalogue, where `configured` is `RandomizerCredentialService.configured_randomizers()` (a randomizer counts only when *every* credential it declares is set). A *stored* preset on an unconfigured randomizer stays valid and editable — its value is re-added to its own editor.
- **Roll time** — resolution happens inside each generator, so a missing credential raises `MissingCredentialError` (a `ValueError`) naming it. There is deliberately **no boundary pre-check**: resolution sits after the `MOCK_SEEDGEN` short-circuit, so dev and CI keep rolling every backend with no credentials at all. `MatchScheduleService.generate_seed` catches `MissingCredentialError` specifically and surfaces its message (its blanket handler still hides raw upstream text); the REST `POST /seeds` answers **400** with the message; `AsyncQualifierService.roll_permalinks` lets it propagate, aborting the batch before any permalink row is written.

To promote a stub to a real backend, replace the `ValueError("… not yet implemented.")` body with an actual generator (drop a preset under `presets/<name>/` if the upstream API takes one, and read any credential with `await self._credential(randomizer, key)` after registering a `CredentialSpec` — see [Adding a randomizer](#adding-a-randomizer-or-preset)) and remove the name from `STUB_RANDOMIZERS`. No changes to `AVAILABLE_RANDOMIZERS` or the dispatch map are needed. `dk64r` and `wwr` were promoted this way.

### Public methods

| Method | Behavior | Returns |
|---|---|---|
| `generate_seed(randomizer: str, preset: Optional[Preset] = None) -> str` | Convenience wrapper over `generate_seed_call` returning just the permalink. | Seed URL (or seed/flags string for Z1R). Raises `ValueError("Unsupported randomizer: …")` for unknown names, `MissingCredentialError` for an unconfigured credential, or a `SeedProviderError` subclass naming what the upstream did. |
| `generate_seed_call(randomizer, preset=None, *, surface=None, triforce_text=None) -> ProviderCall` | Looks up `randomizer` in an internal dispatch map and runs the matching private generator **inside the provider envelope** (below). For `PRESET_AWARE_RANDOMIZERS` (every backend but `ootr`, `smmap` and `test`), a supplied `preset` provides the settings; the other backends (`ootr`, `smmap`, `test`) ignore it and roll their bundled settings. `triforce_text` is embedded by the `TRIFORCE_TEXT_RANDOMIZERS` (`alttpr`) and ignored by the rest. A keyed backend resolves this tenant's credential inside its generator, i.e. after the `MOCK_SEEDGEN` short-circuit. **Use this wherever the roll is persisted** — the returned `ProviderCall` carries the `RolledSeed` (permalink + settings as sent) plus attempts and latency, which is what a `GeneratedSeeds` row records. | `ProviderCall`. |
| `available_randomizers(configured: set[str]) -> list[str]` (classmethod) | `AVAILABLE_RANDOMIZERS` minus any randomizer not in `configured` that declares a credential. Pure and DB-free — the caller passes `RandomizerCredentialService.configured_randomizers()`. Drives the selector surfaces. | Filtered list of randomizer keys. |
| `list_remote_presets(randomizer, *, branch=None) -> list[RemotePreset]` | The presets the randomizer's own API publishes, mapped into storable `settings`. Raises `ValueError` for a randomizer with no catalogue or an unknown branch, `MissingCredentialError` without the key. | `list[RemotePreset]`. |
| `offers_remote_presets(randomizer) -> bool` / `remote_preset_branches(randomizer) -> list[str]` (classmethods) | Membership in and lookup into `REMOTE_PRESET_BRANCHES` — what the Presets tab's import dialog renders from. | `bool` / `list[str]`. |
| `supports_triforce_texts(generator: Optional[str]) -> bool` (classmethod) | Membership in `TRIFORCE_TEXT_RANDOMIZERS`. Four consumers: `AuthService.can_submit_triforce_text`, `TriforceTextService` (both the submit guard and the `seed_generator__in=…` tournament filter), the home Tournaments tab's **Triforce Texts** button (`supporting_tournament_ids` / `open_triforce_dialog`), and the REST `/seeds/randomizers` response field `supports_triforce_texts`. | `bool`. |

### Dispatch targets (private generators)

| Method | Registered in dispatch map | Summary |
|---|---|---|
| `_generate_alttpr` | yes | pyz3r customizer roll from `preset.settings` when a preset is given, else the built-in `presets/alttpr/casualboots.yaml` settings |
| `_generate_ff1r` | yes | Local URL construction: random seed substituted into the preset's flags link |
| `_generate_z1r`, `_generate_z2r`, `_generate_smb3r` | yes | Local string: random seed number + the preset's flags (`_roll_flag_game`) |
| `_generate_aosr` | yes | Local URL: a random seed swapped into the preset's aosrando.surge.sh link |
| `_generate_avianart` | yes | Generate + poll on avianart.games for `preset.settings['preset']`; see [avianart](#avianart) |
| `_generate_sm`, `_generate_smz3` | yes | `POST /api/randomizers/<id>/generate` on sm.samus.link / samus.link with the preset's settings |
| `_generate_smvaria` | yes | Fetch the named settings and skills presets, then `POST /randomizerWebService` as a race seed; see [smvaria](#smvaria) |
| `_generate_smmap` | yes | HTTP POST to maprando.com with `presets/smmap/community_race_s5.json` |
| `_generate_ootr` | yes | HTTP POST to ootrandomizer.com with `presets/ootr/sgl2026.json` |
| `_generate_dk64r` | yes | Task-queue roll against api.dk64rando.com from `preset.settings` (else `presets/dk64r/sgl.json`); needs the tenant's `dk64r.api_key` (Donkey Kong 64) |
| `_generate_smdash` | yes | `GET` dashrando.net `/generate/<tag>?race=1` for `preset.settings['preset']` (else `presets/smdash/sgl26.json`), reading the seed page from the redirect |
| `_generate_wwr` | yes | Local: the preset's reference permalink under a fresh seed name (Wind Waker); see [wwr](#wwr) |
| `_generate_test` | yes | 5-second sleep, then a fixed example URL |

### The provider envelope

Every generator runs inside `application/utils/seed_provider.py`, one execution
contract for all outbound randomizer calls. Randomizer upstreams are third-party
services with no availability promise, and a seed roll is the most time-critical
thing the app does — a race is scheduled, the players are waiting, and the roll
happens minutes before.

| Rule | Value | Why |
|---|---|---|
| Per-attempt timeout | 60s | Bounds a *hang*. An ALTTPR customizer roll genuinely takes tens of seconds under load |
| Attempts | 3 | |
| Backoff | 1s, 2s | Short — someone is waiting |
| Retryable | timeout, connection error, 429, 5xx | The upstream's problem; it may pass |
| Not retryable | 4xx except 429, parse/schema errors, missing credential | Our payload's problem; it will fail identically |

Per-provider overrides live in `PROVIDER_TIMEOUTS` / `PROVIDER_ATTEMPTS`. DK64R
differs on both axes: its whole roll (submit → poll → result) legitimately runs
for minutes, so the outer budget exceeds the poll deadline rather than cutting it
short — and re-running the roll would submit a **second generation task**, so it
gets exactly one attempt.

Failures normalize into a taxonomy that all subclasses `ValueError`, which is what
lets the existing UI (`except ValueError` → `ui.notify`) and REST 400 mapping
surface them unchanged:

`SeedProviderTimeout`, `SeedProviderUnavailable`, `SeedProviderRateLimited`,
`SeedProviderInvalidRequest`, `SeedProviderBadResponse` — each carrying
`provider`, `operation`, `attempts`, `status_code` and the provider's own message.

### Error behavior

`SeedGenerationService` **raises**; it never returns error tuples:

- `ValueError` — unsupported randomizer name, or unknown tournament id (tournament variant). `MissingCredentialError` (a `ValueError`) when the tenant has not configured a credential the backend needs.
- A `SeedProviderError` subclass for anything the upstream did — already retried where retrying could help, and already bounded.

`MatchScheduleService.generate_seed(match_id, actor)` is the boundary that converts everything into a `(success: bool, message: str, seed_url: Optional[str])` tuple for the UI:

| Outcome | Tuple |
|---|---|
| Concurrent click (per-match `asyncio.Lock` in class-level `_seed_locks` already held) | `(False, "Seed generation already in progress for this match", None)` |
| Actor fails `AuthService.can_run_match` | `(False, "You do not have permission to roll a seed for this match", None)` |
| Match already has a seed | `(False, "A seed has already been generated for this match", None)` |
| No preset resolves and `tournament.seed_generator` is unset | `(False, "No seed generator configured for this tournament", None)` |
| Generator not in `AVAILABLE_RANDOMIZERS` | `(False, "Seed generator '…' not found", None)` |
| Match has no players yet (a seed rolls once and its DM would reach nobody; enforced here so REST/MCP are covered too) | `(False, "This match has no players yet — …", None)` |
| Randomizer is in `ASYNC_RANDOMIZERS` | Queues a `ProviderTask` (audit `match.seed_roll_queued`) → `(True, "Rolling the seed — this takes a few minutes. …", None)`; a task already active for the match → the "already in progress" tuple |
| `MissingCredentialError` | `(False, str(e), None)` — names the credential to configure |
| A `SeedProviderError` (upstream down / rate-limiting / rejected the settings) | `(False, str(e), None)` — the envelope's curated message, so the reader can tell "try again in a minute" from "this preset will never roll" |
| Any other exception during generation | `(False, "Seed generation failed. Please check the server logs.", None)` |
| Success | `(True, "Seed rolled for {match label}.", seed_url)` — the label is the matchup or title, never the id |

The UI maps these to `ui.notify` colors and silently skips the "already in progress" case.

## Supported randomizers

| Key | Game | Upstream | Preset file | Credential | Return shape | Notes |
|---|---|---|---|---|---|---|
| `alttpr` | A Link to the Past Randomizer | alttpr.com via [pyz3r](https://github.com/tcprescott/pyz3r) | [`presets/alttpr/casualboots.yaml`](../../presets/alttpr/casualboots.yaml) (fallback when no preset) | — | `https://alttpr.com/h/<hash>` | `ALTTPR.generate(settings, endpoint='/api/customizer')` — the customizer endpoint is required because these presets define a custom item pool and starting equipment |
| `ff1r` | Final Fantasy 1 Randomizer | none (URL built locally) | [`presets/ff1r/sgl2026.json`](../../presets/ff1r/sgl2026.json) (built-in only; no fallback) | — | `https://<version>.finalfantasyrandomizer.com/?s=<seed>&f=<flags>` | Needs a preset. A preset is `{"url": "<flags link>"}`, copied from the site's address bar; its host pins the site version. The roll swaps a random 8-hex-digit seed into `s`. The site builds the game client-side, so the URL *is* the seed. `normalize_preset_settings` refuses a link that isn't `https` on `finalfantasyrandomizer.com` or has no `f` |
| `z1r` | Zelda 1 Randomizer | none (string built locally) | [`presets/z1r/sgl2026.json`](../../presets/z1r/sgl2026.json) (built-in only; no fallback) | — | `"<seed> - <flags>"` (**not** a URL, so tables render it as plain text) | Needs a preset. A preset is `{"flags": "<flag string>"}`; the roll adds a random seed number. Players enter both into the offline tool. A flag string with whitespace is refused on save |
| `avianart` | A Link to the Past (AVIANART, including door rando) | `https://avianart.games/api.php` | none (import from its catalogue) | — | `https://avianart.games/perm/<hash>` | Needs a preset, `{"preset": "<name>"}` (`owner/name` for a user preset). See [avianart](#avianart). Keyless |
| `z2r` | Zelda 2 Randomizer | none (string built locally) | none | — | `"<seed> - <flags>"` | Same shape as `z1r`; seed 0–1,000,000,000 |
| `smb3r` | Super Mario Bros. 3 Randomizer | none (string built locally) | none | — | `"<seed> - <flags>"` | Same shape as `z1r`; seed 0–999,999,999,999 |
| `aosr` | Aria of Sorrow Randomizer | none (URL built locally) | none | — | `https://aosrando.surge.sh/?...&seed=<n>` | Needs a preset, `{"url": "<aosrando.surge.sh link>"}`. The site reads its options and seed from the query and patches the player's ROM in the browser; the roll swaps in a random signed 32-bit `seed` |
| `sm` | Super Metroid Randomizer | `https://sm.samus.link/api/randomizers/sm/generate` | [`presets/sm/`](../../presets/sm) | — | `https://sm.samus.link/seed/<slug>` | Needs a preset: the flat settings object the site's form sends. `seed`, `race` and `spoilerKey` are dropped from it and every roll sends `seed: ""`, `race: "true"`. The slug is the returned `guid` as URL-safe base64. Keyless |
| `smz3` | SMZ3 Combo Randomizer | `https://samus.link/api/randomizers/smz3/generate` | [`presets/smz3/`](../../presets/smz3) | — | `https://samus.link/seed/<slug>` | As `sm`. Multiworld is not supported |
| `smvaria` | Super Metroid VARIA Randomizer | `https://randommetroidsolver.pythonanywhere.com/randomizerWebService` | [`presets/smvaria/`](../../presets/smvaria) | — | `https://randommetroidsolver.pythonanywhere.com/customizer/<seedKey>` | Needs a preset, `{"settings_preset": "<name>"}` plus optional `skills_preset` and `overrides`. See [smvaria](#smvaria). Keyless |
| `smmap` | Super Metroid Map Rando | `https://maprando.com/randomize` | [`presets/smmap/community_race_s5.json`](../../presets/smmap/community_race_s5.json) | `smmap.spoiler_token` | `https://maprando.com<seed_url>` | `multipart/form-data` with a `spoiler_token` part (never defaulted — a leaked token unlocks spoiler logs for race seeds) and a `settings` part carrying the raw preset JSON |
| `ootr` | Ocarina of Time Randomizer | `https://ootrandomizer.com/api/sglive/seed/create` | [`presets/ootr/sgl2026.json`](../../presets/ootr/sgl2026.json) | `ootr.api_key` | `https://ootrandomizer.com/seed/get?id=<id>` | JSON body POST with query params `key`, `version=dev_9.1.38-0` (the generatorDev build SGL 2026 races on), `encrypt=true`, status checked through the envelope's `raise_for_status`; an unset key raises rather than sending `key=None`, and a response with no `id` raises `SeedProviderBadResponse` |
| `dk64r` | Donkey Kong 64 Randomizer | `https://api.dk64rando.com/api` (task queue) | [`presets/dk64r/sgl.json`](../../presets/dk64r/sgl.json) | `dk64r.api_key` | `https://dk64randomizer.com/randomizer.html?seed_id=<seed_number>` | Asynchronous submit → poll → result; see below |
| `smdash` | Super Metroid: DASH | `https://www.dashrando.net/generate/<tag>?race=1` | [`presets/smdash/sgl26.json`](../../presets/smdash/sgl26.json) (fallback when no preset) | — | `https://www.dashrando.net/seed/<key>` | DASH rolls only its own named presets (`packages/core/lib/presets.ts` upstream), so a preset's settings are `{"preset": "<tag>"}`. The call doesn't follow redirects: the 307's `Location` is the seed, and it must be an `https` dashrando.net `/seed/<key>` URL. One attempt only (`PROVIDER_ATTEMPTS`), because upstream saves the seed before answering, so a retry would mint a second one. `race=1` makes the seed page serve a protected ROM, and leaving `spoiler` unset means no spoiler log exists. An unknown tag is a 422 (`SeedProviderInvalidRequest`), and a tag that isn't `[A-Za-z0-9_-]+` raises `ValueError` before any request is sent. Keyless |
| `wwr` | Wind Waker Randomizer | none (built locally) | [`presets/wwr/sgl2026.json`](../../presets/wwr/sgl2026.json) (built-in only; no fallback) | — | a base64 permalink (**not** a URL) | Needs a preset. Players paste the permalink into their own copy of the build it names; see [wwr](#wwr) |
| `test` | — (testing) | none | — | — | fixed example URL after a 5 s sleep | Selectable on tournaments on purpose: it exercises the full UI flow (button spinner, per-match lock, persistence, DMs) without an external call |

### dk64r

`_generate_dk64r(preset=None)` rolls against the DK64 Randomizer **task queue** at `https://api.dk64rando.com/api` — the one backend with an asynchronous submit → poll → result shape. It requires this tenant's `dk64r.api_key` credential (raises `MissingCredentialError` when unset), sent as the `X-API-Key` header on every call. See [Per-tenant credentials](#per-tenant-credentials).

Settings resolve from `preset.settings` (else the committed [`presets/dk64r/sgl.json`](../../presets/dk64r/sgl.json)). The canonical stored shape is `{"settings_string": "<string copied from dk64randomizer.com>"}` — the site's own portable preset format; a full settings JSON dict is also accepted and submitted as-is. An optional `"_branch": "dev"` key routes calls to the `dev` branch/host (default `stable`) and is stripped before anything is sent; an unrecognised value raises **before** the first HTTP call. The flow: `POST /convert_settings` (settings-string shape only — expands it to the full settings JSON), `POST /submit-task` → `task_id`, then `GET /task-status/{task_id}` every 5 s until `finished`. Any non-200 response, a `failed` or unrecognised status, a `finished` whose `result` is not a dict, a missing `seed_number`, or the 10-minute deadline each raise `ValueError`. The `result.seed_number` becomes the player-facing permalink.

**`generate_spoilerlog` is forced to `false` on every roll**, overriding whatever the preset encodes, and the override happens *after* `convert_settings` so a settings string cannot smuggle it back on. Upstream serves the log to anyone holding the seed number (`GET /get_spoiler_log?hash=<seed_number>`) and the seed number *is* the permalink the players are DMed, so a preset with this on hands every racer the item locations. Several of the site's own presets, "Season 5 Race Settings" among them, convert with it set to `true`, so trusting the preset is not a safe default. The forced value lands in the settings snapshot the `GeneratedSeeds` row records.

#### Asynchronous rolls

`dk64r` is the only member of `ASYNC_RANDOMIZERS`, and it does not return a seed from one call. Every surface that rolls one therefore has to decide what to do with a *task*:

| Surface | Behaviour |
|---|---|
| Match board / `MatchScheduleService.generate_seed` | Queues a `ProviderTask`, returns "Rolling the seed…" with no url. The row shows a live `Rolling… 1:24` clock (server-side, so it survives a refresh and every viewer sees it). |
| REST `POST /seeds` | **202** + `{task_id, status}`; poll `GET /seeds/tasks/{id}`. |
| REST `POST /matches/{id}/seed`, MCP `generate_match_seed` | Unchanged shape: `seed_url: null` plus a message saying it is under way. |
| Qualifier pools | **Refused.** Each roll would land independently, so the batch's all-or-nothing property would not hold. Enforced in `AsyncQualifierService`, not only in the preset picker, because a pool created before dk64r became asynchronous can already point at one. |

The split in `SeedGenerationService` is `submit_async_roll` → `AsyncRollSubmission` (the upstream handle, the params a later poll needs, and the settings as sent) and `poll_async_roll` → `AsyncRollPoll` (`seed` set once finished, `None` while queued or running, raises on failure — so "not yet" never reads as "never"). Both share every HTTP call with the blocking `_generate_dk64r`, which is still there for callers that want a seed back.

`seed_roll_worker` (5 s tick) is the only thing that advances a task: it sweeps stale rows, then polls each unfinished task inside its own `tenant_scope`. Because it reads its work from the table, resuming after a restart is the normal path, not a recovery path that only runs after a crash and therefore only breaks after one. Completion goes through `MatchScheduleService.complete_seed_roll` — the same method the synchronous path calls — so a restart changes who finishes the roll, not what finishing means. A terminal transition is claimed with a conditional update, so a duplicate tick cannot write a second seed or DM the players twice.

**No retry.** A failed task stays terminal for a human to re-run: every attempt consumes a real, publicly-numbered seed upstream, and a silent retry loop spends them where nobody is watching.

#### Mocking the queue

DK64R is the one randomizer that does **not** take the `MOCK_SEEDGEN` short-circuit. An instantly-returned permalink would skip the very thing that makes this backend different: upstream takes two to three minutes, and the Generate button holds a spinner for all of it. So `MOCK_SEEDGEN` instead swaps the `aiohttp` session for `MockDK64Session` ([`mock_dk64.py`](../../application/utils/mocks/mock_dk64.py)), an in-process stand-in for the queue. `_generate_dk64r` is unchanged under it — it really converts, submits, and polls on the 5 s cadence while a fake task walks `queued` → `started` → `finished` over `MOCK_DK64_SECONDS` (default 20; the suite pins it to 0). No credential is needed while mocked.

`MOCK_DK64_OUTCOME` picks the ending (`finished`, `failed`, `http_error`, `stuck`) and `MOCK_DK64_BROKEN_STAGE` fails `convert` or `submit` instead, so every error branch above is reachable without a live key. `mock_dk64_presets(branch)` returns a `get_presets`-shaped list for surfaces that read the upstream preset catalogue.

### avianart

`generate_avianart` posts `action=generate&preset=<name>` with `[{"args": {"race": true}}]`, which answers `"status": "generating"` and a hash, then polls `action=permlink&hash=<hash>` every 3 s (40 polls at most) until the status is gone or `finished`. A plain preset builds in about 6 s, a door rando preset in about 15 s. `failure` raises `SeedProviderInvalidRequest`; that is also how an unknown preset name shows up, since generate accepts any name. The whole roll is one attempt (`PROVIDER_ATTEMPTS`) because every generate builds a game, and the provider budget is raised to cover the polls. `action=presets` is the catalogue `list_remote_presets('avianart')` reads (no branches).

### smvaria

VARIA ([theonlydude/RandomMetroidSolver](https://github.com/theonlydude/RandomMetroidSolver)) builds seeds from two of its own named presets: a settings preset (`rando_presets/<name>.json` upstream: item split, progression, area rando) and a skills preset (`standard_presets/<name>.json`: the techniques logic may expect). A Wizzrobe preset is `{"settings_preset": "Season_Races", "skills_preset": "expert", "overrides": {"areaRandomization": "light"}}`; only `settings_preset` is required, and without `skills_preset` the roll uses the one the settings preset names in its own `preset` key.

`generate_varia` posts `randoPresetWebService` (`randoPreset=default`, `origin=extStats`), then the named settings preset, lays the overrides on top, fetches the skills preset from `presetWebService`, and posts every setting as a form field to `randomizerWebService` with the skills JSON as `paramsFileTarget`, `raceMode=on` and `seed=0`. List values are sent comma-joined. The service fills any parameter it doesn't receive with its own default (and says so in `errorMsg`), which is what keeps a roll working when upstream adds a setting. A 400 carries VARIA's reason (an unknown preset, a value outside a setting's allowed list); `status: NOK` raises `SeedProviderInvalidRequest` with `errorMsg` as the provider message. Overrides may not set `seed`, `raceMode`, `preset` or `paramsFileTarget`. One attempt per roll, because each generate stores a seed upstream. A live roll takes about 8 s; the service caps the randomizer at 20 s.

`settings_snapshot` is the form as sent, minus the skills JSON, plus `skills_preset`. There's no catalogue endpoint for settings presets, so the four tournament ones ship as built-ins under `presets/smvaria/` (`season_races`, `varia_weekly`, `sgl23online`, `default`); any other upstream name works typed into the JSON editor.

### wwr

The Wind Waker Randomizer has no hosted generator: each player builds their own ISO from a **permalink**, `base64(VERSION + b"\0" + seed_name + b"\0" + packed option bits)`, zlib-compressed first by tanjo3's dev builds (their permalinks start `eJx`; the 1.10.0 release's start `MS4`). So `_generate_wwr(preset)` is a string operation in [`_seedgen_wwr.py`](../../application/services/_seedgen_wwr.py): decode the preset's reference permalink, swap its seed name, encode it the way it came in. The version and option bytes pass through untouched, so **any build rolls**, including ones Wizzrobe has no tables for.

- **Preset shape.** `{"permalink": "<copied from the randomizer's Permalink box>"}` and nothing else. `SeedGenerationService.normalize_preset_settings`, called by `PresetService` on create and update, decodes it and refuses anything that isn't a permalink (or doesn't fit its vendored build), so a bad paste fails on the Presets tab and not at the desk. The Presets tab gives `wwr` a Permalink field instead of the JSON editor, with the decoded build and a settings summary under it.
- **No preset is an error.** There's no bundled fallback, so a tournament that lost its preset can't quietly roll another season. A missing or broken permalink raises `SeedProviderInvalidRequest`, so the proctor reads why rather than "check the server logs".
- **Seed names** port wwrando's `seedgen.make_random_seed_name` plus `sanitize_seed` (two adjectives, then a noun or a community member's name, e.g. `DirtySkilledGold`). The port consumes the RNG in upstream's order, and `tests/services/test_seedgen_wwr.py` pins it against upstream output for fixed seeds.
- **Builds are exact.** A permalink only loads in the build whose version it carries, commit suffix included (`dev_tanjo3.1.10.7.3_d30d27c`). The DM names the build (`WWPermalink.build` strips the `_<commit>` the release CI appends).
- **Settings summaries need vendored tables.** The option bits carry no names or widths, so `summarize()` reads only builds under `application/services/_wwr_data/<version>/`, written by `scripts/vendor_wwr_build.py` from a wwrando checkout at the release commit (MIT; the licence ships beside the data). The same directory holds the build's seed-name word lists; a build without them borrows the SGL build's, since a seed name never affects validity. Vendored today: `dev_tanjo3.1.10.7.3_d30d27c` (SGL 2026).
- **No seed hash.** The two names on the in-game file select would be the natural "same seed?" check, but with "do not generate spoiler log" on (as SGL 2026's preset has it) wwrando derives them from `md5(permalink + SEED_KEY)`, where `SEED_KEY` is a secret baked into official builds. A hash computed here would disagree with the players' screens. Spoiler-on presets could be computed; that's left for a future event that wants it.
- `settings_snapshot` records `{reference_permalink, version, seed_name}`.

### Seed codes

The file-select symbols racers compare before starting ("Code: Bow · Hookshot · Bombs · Lamp · Cape"). A generator sets `RolledSeed.code` (a list of names) when its upstream hands one back; `complete_seed_roll` stores it on `GeneratedSeeds.seed_code`, the match row carries it as one line (`seed_code`, built by `discord_messages.seed_code_line`), and it shows under the seed in `seed_value_template`, the match sheet and the seed DM (embed description and push text). Readers live in [`_seedgen_codes.py`](../../application/services/_seedgen_codes.py) and return `None` rather than raise: a missing code must never fail or retry a roll.

| Key | Source | Format |
|---|---|---|
| `alttpr` | five bytes at `0x180215` in the customizer response's `patch` | 5 of 32 item names |
| `dk64r` | the finished task's `result.hash` (0–9 digits) | 5 icon names (Bongos … Saxophone, as DK64R's racetime bot names them) |
| `smmap` | `seed_hash` beside `seed_url` in the `/randomize` response | 4 enemy names |
| `smdash` | the `signature` in the `/seed/<key>` page's Next.js payload (one extra GET, no API) | 4 enemy names |
| `avianart` | `fshash` in the finished permlink response, or `spoiler.meta.hash` for door rando presets (whose short names, like `Powder`, are mapped to the ALTTPR list) | 5 item names |
| `sm`, `smz3` | `hash` in the generate response | 4 enemy names |
| `smvaria` | the four bytes the IPS patch writes at SNES `$DFFF00` (ROM `0x2FFF00`), low 5 bits each, into `seed_display.asm`'s 32-name table | 4 enemy names |
| `ootr` | `settingsLog.file_hash` from `GET /api/v2/seed/details`; for an encrypted seed only a key with `locked_encrypt_spoiler_access` gets it, and a seed still generating answers 204 | 5 item names |

`ff1r` (hashed in the browser from the RNG state after the whole randomization), `z1r` (closed-source tool), `wwr` (see above), `z2r`, `smb3r` and `aosr` (all built on the player's machine) have none. `MOCK_SEEDGEN` returns a believable code for the randomizers that have one.

### Pasted seeds (the copy card)

Most randomizers return a URL. `wwr` (a permalink), `z1r`, `z2r` and `smb3r` (`"<seed> - <flags>"`) return a string the player pastes into their own randomizer, and every surface checks which it has (`discord_messages.is_seed_link`, or `^https?://` in a slot template):

- **Seed DM.** A link seed keeps its `[Open your seed]` embed link and button. A pasted seed goes in a code block under `SeedGenerationService.paste_instructions(randomizer, settings)` (the WWR line names the build), with no embed URL, and its button opens the match on the Player tab, where the same seed has a copy button. Discord refuses a non-URL as a link button or embed URL and fails the whole DM, which is what had been happening to Z1R seed DMs.
- **Tables and cards.** `match_slots.seed_value_template(expr)` renders a link, or a truncated monospace value with a copy button (`Quasar.copyToClipboard`, toast on success or failure). `SEED_SLOT`, `SEED_SLOT_READONLY` and the mobile card's seed detail all use it.
- **Test roll dialog.** Shows the full string with a copy button.

## ALTTPR tournament generation and triforce texts

Every ALTTPR match roll carries a community-submitted end-game text from the tournament's approved [triforce text](../features/triforce-texts.md) pool when it has one. `MatchScheduleService.generate_seed` asks `TriforceTextService.get_balanced_text(tournament)` ([`triforce_text_service.py`](../../application/services/triforce_text_service.py)) for any randomizer in `TRIFORCE_TEXT_RANDOMIZERS`: it picks a random *submitter* with approved texts, then a random text of theirs, so every submitter is weighted equally regardless of how many texts they had approved. Texts whose submitter was deleted form their own bucket. (`get_random_text()`, a uniform pick over all approved texts, exists but nothing uses it.) Both are soft-gated: with `TRIFORCE_TEXTS` off, or an empty pool, they answer `None` and the seed rolls with the preset's own text.

`_generate_alttpr` deep-copies the resolved preset's settings (the `casualboots.yaml` fallback without one) and injects the text before generation, so the shared `Preset` row never keeps it:

```python
settings.setdefault('texts', {})['end_triforce'] = "{NOBORDER}\n" + triforce_text
```

(`{NOBORDER}` is an ALTTP text-engine directive; the text itself is up to 3 newline-joined lines of ≤19 characters, validated at submission time.) The settings as sent, text included, land in the `GeneratedSeeds.settings_snapshot`, which is the record of which text a match got. Only match rolls embed a text; `POST /seeds` and qualifier pools don't.

## Presets (DB-backed)

Presets are tenant-authored `Preset` rows (`randomizer`, `name`, `settings` JSON, `description`), managed on the admin **Presets** tab via [`PresetService`](../../application/services/preset_service.py) (CRUD gated by `AuthService.can_manage_presets` — STAFF, `PRESET_MANAGER`, super-admin, or the system actor). A tournament links one through its `preset` FK; seed generation resolves the preset's `randomizer` + `settings`, and the tournament's `seed_generator` column is kept in step with `preset.randomizer` by `TournamentService`.

The committed `presets/` files remain as **built-in starting rows**: `PresetService.import_builtins` (the "Import Built-ins" button) parses them and inserts any not already present (idempotent, matched by `(randomizer, name)`).

### Test rolls

Each row on the Presets tab has a **Test roll** action (`PresetService.test_roll`). It rolls one seed through `generate_seed_call(..., surface='preset_test')`, the same provider envelope, credential lookup and `MOCK_SEEDGEN` short-circuit a match roll goes through. The dialog shows the seed or the provider's error. Nothing is written to `GeneratedSeeds`, so test seeds never mix with match seeds. The audit row (`preset.test_rolled`, with `ok` plus `seed_url` or `error`) is the record. DK64R blocks on its full submit-and-poll roll, so its test can take minutes. A backend outside `PRESET_AWARE_RANDOMIZERS` rolls its bundled settings, and the dialog says so: that test proves the randomizer works, not that the row's settings do.

### Importing a randomizer's own presets

Hand-authoring a settings blob is the fallback, not the expected path. A randomizer that publishes its presets over its API — declared in `SeedGenerationService.REMOTE_PRESET_BRANCHES`, currently AVIANART and DK64R — can be browsed and imported from the Presets tab's **Import from Randomizer** button:

| Step | Call | Notes |
|---|---|---|
| Browse | `PresetService.list_remote_presets(actor, randomizer, branch=None)` | Writes nothing. Delegates to `SeedGenerationService.list_remote_presets`, which returns `RemotePreset(name, settings, description)` with `settings` already in the shape a `Preset` row stores |
| Keep | `PresetService.import_remote_presets(actor, randomizer, names, branch=None)` | Takes **names**, not payloads: the settings are re-fetched upstream rather than trusted from the picker. Idempotent by `(randomizer, name)` like `import_builtins`; audits `preset.imported` with `source` and `branch` |

`REMOTE_PRESET_BRANCHES` maps a randomizer to its catalogue variants (DK64R publishes a separate list per branch, `stable` and `dev`); an empty tuple means one list and no branch select. `offers_remote_presets` / `remote_preset_branches` are the classmethods the UI renders from.

For DK64R each catalogue entry carries a `settings_string` — the site's own portable format — which is stored as `{'_branch': branch, 'settings_string': …}` and expanded through `/convert_settings` at roll time, so an imported preset means whatever the upstream currently means by that string. The catalogue fetch is key-gated like every other DK64R call, and takes the **default** provider budget (60s, 3 attempts) rather than the roll overrides: it is a read, so retrying it cannot enqueue a second generation.

An import is a **copy**, not a subscription. Nothing re-syncs a preset after the upstream edits it, and re-importing a name that already exists is skipped rather than overwritten — a tournament pointing at a preset never has its settings changed underneath it by someone pressing Import.

```
presets/                       # built-in files imported into the Preset table
├── alttpr/
│   ├── casualboots.yaml       # also the _generate_alttpr fallback when no preset
│   ├── hmg.yaml               # SahasrahBot's hmg, raced at SGL 2026
│   ├── openboots.yaml         # SahasrahBot's openboots, raced at SGL 2026
│   └── sglive2025.yaml
├── dk64r/
│   └── sgl.json               # _generate_dk64r fallback (settings-string shape; placeholder value)
├── ootr/
│   └── sgl2026.json           # used by _generate_ootr (hard-coded until coverage expansion)
├── sm/                        # SahasrahBot's Super Metroid presets (tournament/casual, full/split)
├── smdash/
│   └── sgl26.json             # _generate_smdash fallback: {"preset": "sgl26"}
├── smmap/
│   └── community_race_s5.json # used by _generate_smmap (hard-coded until coverage expansion)
├── smvaria/                   # VARIA tournament settings presets by name (Season_Races, VARIA_Weekly, SGL23Online, default)
└── smz3/                      # SahasrahBot's SMZ3 presets (normal/hard/casual, with and without keysanity)
```

For ALTTPR-style files the payload lives under a top-level `settings` key (with sibling `goal_name`/`description`/`customizer` metadata); `import_builtins` stores that `settings` subtree so it is handed to the randomizer unchanged. Other backends store the whole parsed file as `settings`. A `<name>.txt` beside a settings file supplies its description when the file carries none, so a file copied verbatim from upstream (and, for the JSON backends, sent upstream verbatim) never needs editing to have one. Only the `PRESET_AWARE_RANDOMIZERS` (every backend but `ootr`, `smmap` and `test`) read `preset.settings`; `ootr` and `smmap` still open their bundled files.

## Adding a randomizer or preset

1. Drop a settings file under `presets/<randomizer>/` if the upstream API takes one (local generators like `ff1r`/`z1r`/`wwr` ship one too, as their built-in).
2. Add an `async def _generate_<name>(self) -> RolledSeed` to [`seedgen_service.py`](../../application/services/seedgen_service.py) — or `(self, preset: Optional[Preset] = None)` if it consumes preset settings — that calls the upstream service with `aiohttp` (never blocking `requests`) and returns a `RolledSeed(url=..., settings=...)` (the settings as sent become the `GeneratedSeeds.settings_snapshot`; raise the envelope's `raise_for_status` / `SeedProviderBadResponse` for upstream failures). Read any credential with `await self._credential('<name>', '<key>')`, which raises a clear `MissingCredentialError` when the community has not set it (see `_generate_ootr`).
3. Register the name in **both** `AVAILABLE_RANDOMIZERS` and the `generator_map` inside `generate_seed_call` — a method alone is unreachable — and, for a preset-consuming backend, in `PRESET_AWARE_RANDOMIZERS`. Omit that last one and `generate_seed_call` calls the generator with no arguments, so it silently rolls its committed default forever.
4. Register a `CredentialSpec` for each credential in [`application/randomizer_credentials.py`](../../application/randomizer_credentials.py) — that alone puts it on the admin **Randomizer Keys** tab and into `available_randomizers`. No environment variable, and nothing to add to the deployment.
5. Author a preset on the new randomizer and select it as the tournament's Seed Preset in the tournament dialog; the admin schedule's Generate button picks it up with no further wiring. Add a row to the [Supported randomizers](#supported-randomizers) table.

To change which settings an ALTTPR tournament rolls, author or edit a `Preset` on the admin **Presets** tab and select it on the tournament — no code change. For the still-hard-coded backends, edit the path in the `_generate_*` method.

Related: [services.md](services.md) (service layer), [data-model.md](data-model.md) (`GeneratedSeeds`, `Match`, `Tournament` schemas), [frontend.md](frontend.md) (match table internals).
