# Native Brackets

Wizzrobe manages tournament brackets **natively** — generating, progressing, and
standing tournaments in-house — instead of mirroring them from the Challonge API.
It removes the external, quota-bound dependency from the scheduled-restream loop
(schedule matchup → race room → result → advance) that
[online tournaments](online-tournaments.md) already deliver.

Scope: **Staff-managed, tenant-scoped, feature-gated, publicly readable.** A
tournament uses a native bracket **or** a Challonge link, never both. Ships behind
[`FeatureFlag.BRACKETS`](feature-flags.md) — dark by default.

Source: [`models/bracket.py`](../../models/bracket.py),
[`bracket_service.py`](../../application/services/bracket_service.py)
(`BracketService`, a thin composer over the per-concern mixins in
[`_bracket/`](../../application/services/_bracket/)) with
[`bracket_config.py`](../../application/services/bracket_config.py) (config schema)
and [`bracket_engines/`](../../application/services/bracket_engines/) (engines +
standings),
[`bracket_repository.py`](../../application/repositories/bracket_repository.py),
[`api/routers/brackets.py`](../../api/routers/brackets.py); surfaces in
[`pages/admin_tabs/admin_brackets/`](../../pages/admin_tabs/admin_brackets/),
[`pages/brackets.py`](../../pages/brackets.py) (public),
[`pages/home_tabs/brackets.py`](../../pages/home_tabs/brackets.py) (browse tab) and
[`theme/brackets/`](../../theme/brackets/) + [`static/css/brackets.css`](../../static/css/brackets.css).

## Data model

Five tenant-scoped models form one aggregate the lifecycle drives together (full
field tables in [data-model.md](../reference/data-model.md#native-brackets)):

- **`Bracket`** — one **stage** of a tournament. A single-stage tournament has one
  row; a multi-stage tournament has several, ordered by `stage_order`. Carries the
  `format`, the `state` (`DRAFT` → `ACTIVE` → `COMPLETE`, or `CANCELLED` for a
  stage abandoned after it started), and a schema-validated `config` blob.
- **`BracketEntrant`** — the tournament-level roster row that carries an entrant's
  identity across every stage. Placeholder-friendly: a `display_name` now, a
  linked `user` later (one link fixes the entrant in every stage). The indirection
  future-proofs team support.
- **`BracketEntry`** — an entrant's participation within one stage (its `seed`,
  `group_number`, and — once the stage completes — `final_rank`).
- **`BracketMatch`** — one slot in a stage's persisted match graph, carrying the
  `winner_to` / `loser_to` progression pointers, a nullable `best_of` override,
  and optional reported **set scores** (`entry1_score` /
  `entry2_score`, nullable) plus a **`forfeit`** flag. `report_result` /
  `override_result` accept the scores and forfeit flag; the reported winner must
  carry the strictly-higher score **unless** `forfeit` marks a DQ / walkover
  (win-only reporting, with null scores, stays valid). Per-round display chrome
  (scheduled time, and the default `best_of`) lives in `Bracket.config['rounds']` keyed by round
  number, not on the match.
- **`BracketMatchGame`** — one game of a matchup's best-of-N series. `match` is a
  nullable OneToOne (`SET_NULL`) to the scheduled `Match`; rows are created lazily
  at schedule time and `game_number` is assigned by the service, never by a
  caller.

## Formats and multi-stage chaining

Four formats, selected per stage via `Bracket.format`
([`BracketFormat`](../reference/data-model.md#bracketformat)):

- **Single elimination** — standard seeded bracket with structural byes.
- **Double elimination** — winners bracket (positive rounds) + losers bracket
  (negative rounds, start.gg's convention), a grand final, and a **conditional
  reset** match persisted but activated only when the losers-bracket entrant wins
  the first grand final.
- **Swiss** — per-round pairing from live standings; no elimination.
- **Round robin** — every within-group pair meets once; the field is optionally
  split into balanced groups.

**Multi-stage tournaments** chain stages within one tournament (e.g. round-robin
groups → single-elimination playoff). Stage 0 runs to completion, writing each
entry's `final_rank`; a later stage carries an `advancement` rule in its config —
on the stage being drawn **into**, never the one drawn from, which
`validate_bracket_config` enforces by rejecting the key on stage 0 — and is seeded
from the prior stage's ranks by `advance_stage`, drawing the top
`count` (per source group or overall), skipping dropped entries, and laying out
seeds by the `snake` (default) or `preserve` policy. Advancement enrolls **fresh
`BracketEntry` rows pointing at the same `BracketEntrant`**, so identity carries
across stages.

## Config: what a stage is allowed to be asked

`Bracket.config` is a closed `extra='forbid'` blob
([`bracket_config.py`](../../application/services/bracket_config.py)) — but a
blob cannot see the stage it belongs to, so `validate_bracket_config` takes the
`fmt` and `stage_order` from the service and adds the checks the schema cannot
make:

- **`swiss_rounds` / `group_count` / `grand_final_reset` only on their own
  format.** A key stored against another format is not merely useless — it reads
  back as a setting the organizer believes is in force. A key sitting at its
  schema *default* passes, because the normalizer (`model_dump(exclude_none=True)`)
  injects every non-None default, so a stage's own stored blob is re-submitted
  carrying keys nobody typed.
- **No `advancement` on stage 0** — both readers look backwards from it, so a
  rule there is read by nobody, forever and silently.

`default_best_of` is the stage-wide series length, consulted by `resolve_best_of`
between the per-round value and the bare `1` fallback. It exists because the
per-round editor is derived from the generated match graph and so cannot be
filled in *before* Start — without it, every stage is unavoidably Bo1 for the
window in which its opening round is generated and announced.

`set_round_metadata` deliberately validates the **shape only** (no `fmt`, no
`stage_order`): it is the single edit allowed after a stage starts, and must not
start refusing over a key it is not touching and the caller can no longer reach.

## Previewing the draw

`preview_draw(bracket_id)` is the read-only twin of `start_bracket` — the same
engine, the same seeding rule, the same bye materialization, run in memory. It
returns unsaved `BracketMatch` rows carrying synthetic **negative** ids, shaped
exactly like the ones generation would persist, so the ordinary renderer draws
them and what an organizer approves is what they get. Start is the draw, the
publish and the notification at once; without this the only way to find out what
a seeding produces is to publish it. A seeding that could not start is *reported*
(`DrawPreview.error`) rather than raised, so the preview explains the problem the
Start would have hit.

## Architecture: generate-then-persist

The pairing/progression **engines are pure structural code** (no ORM, no async):
they turn a seeded field + validated config into a description of a match graph.
`BracketService` maps that onto `BracketMatch` rows and persists it. The engine is
invoked only at two moments:

1. **`start`** — generate the whole graph (elimination / round robin) or pair
   Swiss round 1.
2. **Per Swiss round** — pair the next round from current standings.

At all other times **the persisted `BracketMatch` rows are the source of truth.**
After `start`, elimination advancement is plain **pointer-following** — a graph
walk over stored rows, never a re-run of the engine: recording a result completes
the match, then pushes the winner into `winner_to` (and the loser into `loser_to`)
at the recorded `*_to_slot`, settling downstream walkovers and auto-completing the
stage when the final resolves.

### Engine composition

Engines register with the shared `tournament_strategies` registry under the
`bracket_format` kind (`@register_strategy('bracket_format', …)`); importing
[`bracket_engines/`](../../application/services/bracket_engines/) auto-imports every
sibling and each self-registers, so `get_bracket_engine(fmt)` resolves one by
`BracketFormat` value. Two engine shapes ([`base.py`](../../application/services/bracket_engines/base.py)):

| Format | Module | Shape | Source |
|---|---|---|---|
| Single elimination | `single_elimination.py` | generative (`generate`) | in-house (`standard_seeding` / `next_power_of_two`) |
| Double elimination | `double_elimination.py` | generative | structure ported in spirit from the MIT `smwa/python-tournaments` |
| Round robin | `round_robin.py` | generative (circle method, snake groups; no progression pointers) | in-house |
| Swiss | `swiss.py` | pairing (`pair_round`, per round) | thin adapter over the MIT **`swisspair`** library |

Round robin, Swiss re-pairing, stage-completion ranking, and the public page's
live display all share one ORM-free standings pass
([`standings.py`](../../application/services/bracket_engines/standings.py)):
`compute_standings(refs, results, config)` over opaque `int` refs, computing match
points and a configurable tiebreaker chain (`buchholz`, `omw`, `head_to_head`)
into 1-based competition ranks (unresolved ties share a rank and list each other
in `tied_with` for a staff override).

The **Swiss adapter is made deterministic** despite `swisspair`'s internal
tie-break RNG by encoding a rank tiebreak into the integer points, so the min-cost
no-rematch matching is unique and reproducible.

## Scheduling seam and Challonge exclusivity

A bracket match spans `best_of` games, and **every game is its own scheduled
`Match`** — that link is `BracketMatchGame.match`, a OneToOne, so a `Match` never
backs more than one game. A best-of-1 is simply a series with a single game, so
there is one code path rather than a special case. `BracketService` mirrors the
Challonge integration method-for-method:

| Bracket (native) | Challonge (mirror) |
|---|---|
| `list_open_matches_for_user` | `list_unscheduled_matches_for_user` |
| `schedule_bracket_match` (→ `create_match` / `submit_match_request`, write a `BracketMatchGame`) | `schedule_challonge_match` |
| `link_match_to_bracket_match` / `unlink_match` (staff attach an existing `Match`) | — |
| `advance_if_linked` (confirmed `Match` → settle the game) | `push_result_if_linked` |
| `release_game_if_linked` (cancelled/deleted `Match` → free the slot) | — |

### Cancelling or deleting a game frees its slot

A cancelled or deleted `Match` hands its `game_number` back, so the matchup stays
schedulable. `MatchService._remove_match` calls
`BracketService.release_game_if_linked` **before** the row is deleted — after
that, `SET_NULL` has fired and the game is unreachable from the match. Three
rules:

1. Only a **`SCHEDULED`** game is released. A `COMPLETE` game keeps its result
   *and* its consumed slot — the bracket has already advanced on it. That is also
   what makes the method re-entrant with the clinch: `_clinch` marks its leftover
   games `CANCELLED` *first*, so a clinched Bo3 does not get game 3's slot back.
2. Release means **deleting the row**, not marking it `CANCELLED` —
   `next_game_number` treats a cancelled number as consumed, so a `CANCELLED` row
   would free nothing. Mirrors `unlink_match`; the audit log is the record.
3. It is **best-effort** at the call site: a bracket failure must not leave a
   match the staff asked to cancel half-cancelled.

The release audits `BRACKET_GAME_RELEASED`, publishes the mirror event, and — via
`notify_matchup_reopened` — tells both entrants to rebook. Slots stranded by older
code are cleared by `scripts/release_orphaned_bracket_games.py` (dry-run by
default, `--apply` to act).

### One correction path for a settled result

Once a game is `COMPLETE` the bracket has advanced on it, so re-recording the
`Match`'s ranks would leave the series' win count, the downstream slots and any
completed stage holding the old answer. `MatchService.record_match_result`
therefore consults `match/bracket_result_guard.py` and **raises**, naming the
stage and pointing staff at **Results → Override** (`override_result`), which
re-advances properly — the same shape as the SpeedGaming `match_source_guard`.

### Best-of-N series

`best_of` resolves **per matchup**: `BracketMatch.best_of` → the round's
`Bracket.config['rounds'][N]['best_of']` → `1`. It is semantic, not chrome —
the scheduler and the clinch both read it. Set it before scheduling; it is
rejected once games exist, because each game's title carries "Game 2 of 3".

The bracket match stays **OPEN for the whole series** and completes only on the
clinch, which delegates to `_record_result`. Pointer-following, the grand-final
reset, walkover settling, and stage completion are therefore reused unchanged —
`entry1_score`/`entry2_score` simply become games won.

Two paths settle a game, so settling is a **compare-and-swap** on the game row:
`RaceRoomService.record_finish` (a racetime finish never *confirms* a match, so
`advance_if_linked` alone would never fire on an auto-room tournament) and
`advance_if_linked` on the serial `discord_queue`. Counting one game twice would
clinch a Bo3 off a single game.

On the clinch, unplayed games are cancelled and their `Match` rows go through
`MatchService.cancel_match`, so players and crew are told. `report_result` /
`override_result` reconcile the same way — a staff-forced 2-0 must not strand a
pre-scheduled game 3.

### Holding the next game's race room

Game N's room must not auto-open while game N-1 is still being raced. The hold is
`BracketService.held_match_ids`, applied in `race_room_worker._tick` over the
whole candidate set (two queries, not one per match) — and it keys on
**`Match.finished_at`, not game state**, because a double forfeit never produces
a COMPLETE game row and would deadlock the series silently.

Holding alone is not enough: the poll's scan starts at `now - 15min`, so a game
whose slot slipped further while the previous game ran long has dropped out of it
for good. `release_next_game` pushes at game-end, reusing
`RaceRoomService.auto_open_if_eligible` (which keeps the lead-window guard, so it
is a no-op when the next game is still legitimately far out). A wider
`SERIES_GRACE_MINUTES` scan backstops a process that died in between.

A prior game whose `Match` was deleted **fails open** — the next game is
released, with a warning. Failing closed produces a room that never opens, with
nothing surfaced anywhere, which is the worse failure. Seed generation needs no
separate hold: it hangs off room creation.

Only OPEN matches whose **both** entrants resolve to a linked `user` are
schedulable. When a linked match is confirmed, `advance_if_linked` maps its winner
back to the winning `BracketEntry` and reports it — advancing the native bracket
the same way `push_result_if_linked` advances Challonge.

### Who may schedule, and the manual-request lockout

A bracket-run tournament schedules **only the matchups the bracket produced**.
That is one toggle plus two authorized callers:

- **`Tournament.allow_player_match_requests`** (default `True`) is turned off
  automatically by `BracketService.create_bracket` (stage 0 only, so a later stage
  cannot undo a staff re-open) and by `ChallongeService.link_tournament`. The way *out* is
`ChallongeService.unlink_tournament`, exposed as **Unlink** on the Challonge
admin tab: it clears the link and drops the local mirror (the remote bracket and
the community's scheduled `Match` rows are untouched), which is what makes moving
an existing tournament onto a native bracket possible at all. While it
  is off, `MatchService.submit_match_request` refuses the tournament —
  `assert_player_requests_allowed` in
  [`match_request_guard.py`](../../application/services/match/match_request_guard.py)
  is the enforcement, and `UserMatchDialog` filters those tournaments out of its
  dropdown so the choice is never offered. Staff can turn it back on per
  tournament from the tournament editor.
- **`schedule_bracket_match` gates for itself** and routes by actor: Staff and the
  tournament's admins go through `MatchService.create_match` (and may set a stage
  and crew); the matchup's **own two entrants** go through
  `submit_match_request` with `from_bracket=True`, which is what bypasses the
  toggle — scheduling through the bracket is the path the toggle exists to force.
  A non-privileged caller passing staff-only fields is rejected rather than having
  them silently dropped. `ChallongeService.schedule_challonge_match` passes the
  same flag.

Players see their pending matchups on their dashboard
([`pages/home_tabs/player.py`](../../pages/home_tabs/player.py)) and book them
through the shared
[`BracketScheduleDialog`](../../theme/dialog/bracket_schedule_dialog.py) — the same
dialog the staff bracket view opens, in its player mode.

Booking is final on the player's side: the match is created at once, the
opponent gets the acknowledgment request, and their only way to change it is a
reschedule request staff decide. The dashboard copy and the booking toast say
it's booked and the opponent gets a message, and add "can ask staff to move it"
only when the tournament takes reschedule requests (`allow_reschedule_requests`).
The dialog defaults to the next whole hour (`next_whole_hour_local`, stepped in
UTC so a spring-forward night never yields a missing wall time), and
`submit_match_request` refuses a time that has passed and an archived tournament
(`is_active` off), the bracket path included.

The dialog passes `game_number`, the game it says it is booking. It never picks
the slot; it makes a stale caller fail instead of quietly booking the next game.
When that game was booked meanwhile, `schedule_bracket_match` raises
`AlreadyBookedError` (a `ValueError`) naming who booked it (the
auto-acknowledged player on the game's match) and when; the dialog closes and
refreshes. The error's `str()` states the time in UTC, as REST does, and the
dialog rebuilds it on the viewer's clock with `describe()`. A slot that is gone
for another reason (cancelled on the clinch, played) is a plain `ValueError`,
and a caller without `game_number` (REST) gets the plain "already scheduled"
refusal once every slot is taken.

Going the other way, staff can attach a match scheduled in the ordinary editor to
the matchup it settles: `link_match_to_bracket_match` writes the
`BracketMatchGame` for an existing `Match` and `unlink_match` detaches one that has
not been played, with the picker in the admin match dialog
([`theme/dialog/_match_bracket_link.py`](../../theme/dialog/_match_bracket_link.py)).
The link validates that the match's **player set equals the two entrants' users**:
`_winner_from_ranks` maps the winner by `user_id`, so a mismatched link would
settle nothing and strand the series with no visible cause.

A tournament uses a native bracket **or** a Challonge link, never both:
`BracketService._ensure_no_challonge_link` rejects a native bracket on a
Challonge-linked tournament, and the symmetric guard lives in
`ChallongeService.link_tournament`.

### The Challonge side of the seam

- **Result push retries once** (`_challonge_results.py`, `push_result_if_linked`)
  on a timeout, dropped connection, 429, 502–504 or Cloudflare's 520–524 origin
  errors, which usually clear on the next request. It's safe because the push
  re-reads the Challonge match and leaves an already-complete one alone, covering
  a PUT that did land. Both attempts plus any `Retry-After` wait are capped to fit
  the confirm flow's 90-second budget, and a failed post-push re-sync never undoes
  the push.
- **Self-signup spends quota** (`_challonge_signup.py`). Every sign-up or
  withdrawal on a Challonge-managed roster costs one request of the community's
  monthly Challonge quota, hence `ROSTER_CHANGES_PER_HOUR` (6 per player). Entrants
  Wizzrobe adds carry `wizzrobe-user:<id>` in Challonge's `misc` field so a sync
  maps them before the player accepts Challonge's invite. A withdrawal Challonge
  answers with 404 (staff already removed the entry there) completes locally
  rather than leaving a ghost entry.

## Lifecycle, audit & events

Every write is Staff-gated (`AuthService.is_staff`), audits an
`AuditActions.BRACKET_*` action, and publishes the mirror `EventType.BRACKET_*` on
the [event bus](event-system.md): `BRACKET_CREATED`, `BRACKET_STARTED`,
`BRACKET_MATCH_COMPLETED`, `BRACKET_ADVANCED` (next Swiss round),
`BRACKET_COMPLETED`, `BRACKET_STAGE_ADVANCED`, `BRACKET_ENTRANT_ADDED`,
`BRACKET_ENTRANT_DROPPED`, `BRACKET_ENTRANT_UPDATED` (the entrant's user link — the
third roster mutation beside add and drop), `BRACKET_ENTRY_RETIRED` (a field
shrinking mid-stage), `BRACKET_CANCELLED` (a stage abandoned outright — terminal,
but with no ranking and no champion, so nothing can advance out of it), and the
per-game `BRACKET_GAME_SCHEDULED` /
`BRACKET_GAME_COMPLETED` / `BRACKET_GAME_CANCELLED` plus
`BRACKET_GAME_LINKED` / `BRACKET_GAME_UNLINKED` for the staff link/unlink of an
existing `Match`, and `BRACKET_GAME_RELEASED` when a cancelled or deleted `Match`
hands its slot back. Four actions are audit-only, with no event: `BRACKET_UPDATED`
(stage config, round metadata, series length), `BRACKET_DELETED`, and the
per-stage roster edits `BRACKET_ENTRY_ADDED` / `BRACKET_ENTRY_REMOVED`. Two writes are **not** Staff-only: scheduling (see
[Who may schedule](#who-may-schedule-and-the-manual-request-lockout)), and `advance_if_linked`, because a Proctor or the system user confirming a linked match must still advance the bracket, as `push_result_if_linked` does for Challonge. Method-level detail:
[services.md → BracketService](../reference/services.md#bracket_servicepy--bracketservice).

## Notifications

**One player-facing message, and series context on the rest.** The rule is narrow
on purpose: *a DM is sent when it asks the recipient to act.*

- **"Your <round> matchup is ready to schedule"** — to both entrants, whenever a
  matchup becomes bookable: the `PENDING → OPEN` transition in `_settle_match`,
  the grand-final reset opening, each round-1 / Swiss pairing at generation, and
  (as a *rebook* variant) a game whose slot was released. Keyed on the
  **transition**, so a matchup whose two slots fill at different times is
  announced once. Scheduling is the only thing in a bracket that nobody else can
  do for the entrants — `allow_player_match_requests` is off for bracket-run
  tournaments — which is what earns it a DM.
- **No advancement and no elimination message.** The bracket shows both. A
  losers-bracket drop needs no carve-out either: that entrant simply gets the
  ready DM when their losers matchup opens, by the same trigger as everyone else.
- **Series context on the existing match DMs** — `Round: Semifinals · Game 2 of
  3 · Series 1-0` leads the info block of `scheduled` / `rescheduled` /
  `checked_in` / `cancelled` / `state_changed` / `seed` DMs, via
  `BracketService.match_dm_context`, so a bracket game never reads as a casual
  scheduled match. The cancellation DM adds the release follow-through: "the
  matchup is open to reschedule."

All of it goes out on `discord_queue` (which re-binds the tenant scope), respects
`dm_notifications` (inside `send_dm`), and is best-effort — a Discord failure never blocks the
advancement that fired it. **No feature-flag check**: these fire from inside
bracket code that only runs when a bracket exists, and an `is_enabled` read inside
a service transaction is what [feature-flags.md](feature-flags.md) forbids.

## Gating

`BRACKETS` gates every entry surface — never a DB read inside a service
transaction:

- **Public pages** — `@public_page(..., feature=FeatureFlag.BRACKETS)` on both
  bracket routes (404 when off).
- **Admin tab** — `is_staff and FeatureFlag.BRACKETS in live` in `pages/admin.py`.
- **Static spectator routes** — an explicit `is_enabled(FeatureFlag.BRACKETS)`
  check in [`pages/static_brackets.py`](../../pages/static_brackets.py) (404 when off).
- **REST** — `require_feature(FeatureFlag.BRACKETS)` on the `/brackets` router
  mount (whole router 404s when off).
- **Service** — `@requires_feature(FeatureFlag.BRACKETS)` on `BracketService`'s
  public entry methods, so a caller that skips the surfaces above still gets a
  `FeatureDisabledError`. The soft integration points called from match flows
  (`advance_if_linked`, `release_game_if_linked`, `held_match_ids` in the race-room
  worker) deliberately do not raise.

## Public access — anonymous, and reachable

A bracket is what a tournament shows the world, so the two view routes are
[`public_page`](../reference/authentication.md#public_page), not
`protected_page`: they never join `protected_routes`, so `AuthMiddleware` lets a
signed-out request through instead of redirecting it to `/login`. Everything else
about the gate is unchanged — the tenant must resolve, `BRACKETS` must be live for
it, and every write/staff affordance still sits behind `AuthService.is_staff`,
which is `False` when `user is None`. What an anonymous visitor sees is the
read-only surface: entrant display names, seeds, scores, standings, and each
game's scheduled time.

**DRAFT stages are staff-only.** A stage in `DRAFT` is unpublished — it exists so
staff can author and seed a field before announcing it — so on every public
surface it reads as absent to everyone else: omitted from the browse tab and the
stage index, and the shared not-found page (HTTP 404) on the detail route. A
`CANCELLED` stage is withdrawn by the same rule. **The rule lives in
`BracketService`**, not in the pages: `is_published(bracket)` and the
viewer-aware reads `list_visible_brackets(viewer, tournament_id)`,
`list_all_visible_brackets(viewer)`, `get_visible_bracket(viewer, bracket_id)`
and `get_visible_tournament(viewer, tournament_id)`, where `viewer=None` means
signed out and staff see everything. Every surface that shows stages to anyone
else calls them — the interactive pages, their static twins (with no viewer),
the join door, the home browse tab, REST (`/api/brackets`, its `/{id}/…` reads
and `/entrants`) and the MCP bracket tools — so a new surface cannot forget the
filter. It used to be a presentation helper the pages applied, and REST and MCP
handed drafts, config and seeded field to any token holder. Staff keep the
unfiltered reads for Admin → Brackets and lifecycle code.
`tests/services/test_bracket_visibility.py`, `tests/api/test_brackets.py` and
`tests/mcp/test_mcp_reads.py` cover it.

**A tournament with nothing published is not public either.** The stage index
(interactive and static) for a tournament with no visible stage is a 404 to
everyone but staff — the same page as an id never used. It used to print the
tournament's name for any id, so walking ids signed out listed a community's
unannounced events ("Bracket Demo — Draft", next season's cup) even though their
stages stayed hidden. Malformed and out-of-range ids (`/brackets/abc`,
`/brackets/99999999999`) are the same 404 on every bracket route
(`parse_route_id`), rather than a raw 422 or an `INT`-range 500. The static
routes also answer `HEAD`, which some link unfurlers probe with first.

Reachability is the other half, and there are two ways in. Home is the only page a
signed-out visitor lands on, so the **Brackets** tab is the browse path — added
whenever `BRACKETS` is live, **signed in or not**, which is why `pages/home.py`
resolves the live flag set before assembling the tab list rather than inside its
signed-in branch. The **schedule** is the other: every game is an ordinary
`Match`, so a bracket-scheduled row carries a "<stage name> · Game N" link into
the bracket view on the public schedule, the admin schedule and the player
dashboard alike (they are one `MatchTableView`). Both are detailed in
[frontend.md](../reference/frontend.md).

Neither surface is published to search engines: the app serves a blanket
`robots.txt` (`frontend.py`) and `BaseLayout` stamps a `noindex, nofollow` meta.
Signed-out means shareable by link, not indexed. Page routes are **not** rate
limited (`api/rate_limit.py` is mounted on the REST router only) — a known gap
that predates public brackets, since the schedule was already anonymous.

### Static spectator views — the link you send a stream

The two `public_page` routes above are NiceGUI, which means **one websocket per
open tab** for as long as it stays open, on an app that runs a single worker
([scaling-roadmap.md](../scaling-roadmap.md)). That is the right trade for the
people running the tournament and the wrong one for a bracket link posted to a
stream or a Discord announcement, where the readers outnumber the staff by
three orders of magnitude and none of them need a dialog.

So each view has a **static twin**: the same bracket, rendered server-side into a
plain HTML document with no client framework, no socket, and nothing to keep
alive.

| Static route | Interactive twin |
|---|---|
| `/live/tournament/{tournament_id}/brackets` | `/tournament/{tournament_id}/brackets` |
| `/live/brackets/{bracket_id}` | `/brackets/{bracket_id}` |

A third static page, the community-wide **Watch page** at `/live`
([`pages/static_watch.py`](../../pages/static_watch.py)), shares this cache and
its invalidation and links each tournament's static index; see
[frontend.md](../reference/frontend.md). `middleware/public_cache.py` strips
`Set-Cookie` and drops `Cookie` from `Vary` on all three, so a CDN can serve one
entry to every reader.

They are plain FastAPI routes in
[`pages/static_brackets.py`](../../pages/static_brackets.py) — a `@public_page`
would mint the very client this exists to avoid — and each interactive page links
to its twin ("Shareable spectator view"), with the static page linking back.
Everything `public_page` does that still applies is done explicitly: the tenant
comes from `TenantMiddleware` (so `/t/<slug>/live/…` works, and a tenant-less
path 404s), `BRACKETS` must be live, and the service's viewer-aware reads,
called with no viewer, keep DRAFT and CANCELLED stages off the surface. Page-view telemetry is deliberately
**not** recorded: a DB write per spectator is exactly the cost being avoided, and
the interactive pages still report.

**Rendering** lives in
[`theme/brackets/static_view/`](../../theme/brackets/static_view/) and is pure —
`markup.py` (cards/sections), `data_tables.py` (standings/pairings/crosstables),
`document.py` (shell + the two page renderers). It emits the *same* class names
and DOM shape as the interactive renderer, so `static/css/brackets.css` styles
both and the desktop/phone split is the stylesheet's existing media query rather
than JavaScript; it consumes the *same* pure inputs (`layout_section`,
`build_context`, `compute_standings`, `results_from_matches`), so geometry, match
numbering, placeholders and standings cannot drift between the two views. What is
gone is everything interactive: no match dialog, no zoom, no view toggle, no live
subscription. Tabs and expansion panels become `<details>`; the single inline
script is the hover-run highlight plus the dark-mode class. This is the one
bracket renderer that builds markup by hand instead of through `ui.label`, so
**every user-controlled string goes through `html.escape`** — asserted in
`tests/theme/test_static_bracket_view.py`.

**Caching** is two layers over one render:

* *In-process* ([`HtmlPageCache`](../../application/utils/html_cache.py)), keyed
  by tenant + page. Invalidated by a sync event-bus subscriber on every
  `bracket.*` and `match.*` event for that tenant — a version bump, not a scan,
  because the callback runs inside `publish`. A 30s TTL is the backstop for state
  no event announces (a card whose derived status turns "imminent" as the clock
  moves). A tenant-less event clears the cache outright rather than guessing.
* *HTTP* — `Cache-Control: public, max-age=30, stale-while-revalidate=300` plus a
  strong `ETag`, so the page's own meta-refresh (60s on a stage, 300s on the stage index) usually costs a 304 with no
  body, and a CDN can absorb a burst without touching the app.

`public` caching and NiceGUI's session cookie are incompatible, and NiceGUI mints
one on every request that reaches the app — so
[`middleware/public_cache.py`](../../middleware/public_cache.py) strips
`Set-Cookie` from exactly these responses. It is installed on the *wrapping*
FastAPI app in `frontend.init` because that is the only place outside the session
middleware `ui.run_with` adds. Nothing is lost: these pages never read the
session.

Verified end to end against the running app: zero websockets, five to six
requests per page load (the document plus cached CSS/fonts), a 304 on
revalidation, no `Set-Cookie`, and a reported result changing the ETag on the
next request.

## Presentation

The public page and the admin Results dialog share one in-house renderer,
[`theme/brackets/`](../../theme/brackets/), drawing the canonical
Challonge/start.gg/Liquipedia grammar: connector-lined match cards, seeds, a
right-aligned score cell (winner accented, loser dimmed, "FF" for a forfeit),
sticky round headers, and bye / "Winner of 7" placeholders. Module-by-module
detail — the pure layout walker, the cards/tables/dialog/live modules, and the
`--bracket-*` CSS variables — is in
[frontend.md](../reference/frontend.md#bracket-renderer-themebrackets).

The bracket-domain rules that renderer enforces:

- **Entrant discs show Discord avatars.** An entrant linked to an account with a
  cached avatar hash (`User.discord_avatar`) gets their picture; everyone else
  keeps the deterministic hue + initial-letter disc. Both are in the DOM — the
  image sits over the letter, so a hash that went stale between renders 404s and
  the letter shows through instead of a broken image. The map is built once per
  bracket by `theme.brackets.entry_avatars(entrants, entries)` off the `user`
  relation `BracketService.list_entrants` prefetches, and covers the 2-D cards,
  the phone accordion, the standings tables and their static twins.
- **Live match state on the cards.** Every card carries the *derived* status from
  [`match_status.py`](../../application/services/match/match_status.py) — the
  vocabulary the schedule table, REST payloads and Discord embeds share, so a card
  can never say "Scheduled" for a match the schedule calls "In Progress".
  `BracketService.matchup_live_state(matches)` resolves it plus a watch link per
  matchup, reading the racetime rooms for the whole field in **one batched query**.
  The statuses are `LIVE` (accent border + a pulsing pill linking to the stream,
  else the room), `CHECKED_IN`, `AWAITING_RESULT` (played, no winner yet — so a
  reader knows the bracket is not stuck) and `NEEDS_RESCHEDULE` (an underway series
  with nothing booked). Anonymous viewers see all of it, watch link included.
- **The page knows who is looking.** On the interactive view a signed-in
  viewer's own entry carries a **You** chip and every card it appears on gets an
  accent outline (`viewer_entries`, from the entrant's linked account), and an
  unfinished matchup shows its next booked game time on the card's bottom edge
  (`booked_times`, the earliest scheduled unfinished game, on the viewer's
  clock, with the full time in a tooltip). The stage's configured rules
  (`config_summary`) are shown to staff only, here and on the static twin. A
  member keeps home's sections in the drawer (see
  [frontend.md](../reference/frontend.md#layout-system-themebasepy)).
- **A bye reads the same everywhere.** The empty side of a completed matchup is
  **BYE** on the card and in the dialog it opens (`slot_display_name`); it used
  to be TBD in the dialog.
- **Repaint filters on `tournament_id`.** `live.py` also subscribes to the
  `MATCH_*` lifecycle events; their payloads carry no `bracket_id` and the
  subscriber is sync and non-blocking (it runs inside `publish`), so it filters on
  the `tournament_id` every `MATCH_*` payload does carry. The wider net is absorbed
  by the page's debounce — the alternative is a query in a sync callback.
- **Round window vs game time.** `Bracket.config['rounds'][N]` carries
  `scheduled_at`/`scheduled_end` — the window the *round* runs in; `Match.scheduled_at`
  is when a specific game happens. They may differ, so the round header reads
  "Round time: …" while the card and dialog carry the game's own time.
- **The round window is a hard bound on suggestions.** `BracketScheduleDialog`'s
  Suggest a time button (the same `render_suggest_time_button` every scheduling
  dialog shares) passes its `bracket_match_id` to
  `MatchSuggestionService.suggest_match_time`, which loads the matchup's round
  entry and only offers slots the match fits inside **end to end** — the
  occupancy/availability search runs *within* the window rather than against it.
  Nothing fitting raises a `ValueError` naming the round window, shown as a
  warning notification in place of a filled-in time — the Date/Time inputs
  default to "now" until the button is clicked. Either half may stand alone (a
  start alone is a floor, an end alone a deadline), and an unscheduled round is
  unbounded, so rounds configured before `scheduled_end` existed behave exactly
  as before. This bounds the *suggestion* only — `schedule_bracket_match` still
  accepts any time staff or players type in.
- **Exactly one view renders at a time.** The 2-D bracket and the per-round
  accordion draw the same graph, so CSS shows one or the other: the 2-D view from
  `md` up, the accordion below it with a **List / Bracket** toggle opting into the
  horizontally-scrolling 2-D view. The chosen view is held per client *outside* the
  `@ui.refreshable`, so a live `BRACKET_*` rebuild does not snap the reader back to
  the list.
- **Results-dialog lists are a fallback.** Wherever the visual embed renders
  (elimination formats) the flat Open / Completed lists sit in default-closed
  expansions — a 32-match stage otherwise buries the dialog's own actions. Swiss and
  round robin have no embed, so there the lists stay open: they are the only surface.
- **`complete_stage` asks first** ([`ConfirmationDialog`](../../theme/dialog/confirmation_dialog.py)):
  it writes every entry's `final_rank` and locks the stage with no un-complete.

## Correctness harness

Pairing and progression are correctness-critical, so the engines carry a
dedicated harness under [`tests/services/`](../../tests/services/): `_bracket_sim.py`
(a reusable, engine-agnostic simulator — `validate_graph` asserts a generated
graph is internally consistent, `simulate_*` plays it to a champion), structural
invariant tests per engine, standings/advancement/multi-stage coverage, a tenant
leak test, and REST lifecycle + auth-matrix tests. Swiss is additionally
**cross-validated against bbpPairings** (a FIDE-grade Dutch engine) on the hard
constraints — no rematch, ≤1 bye, everyone paired — by serializing each scenario
to TRF(x) and feeding it to the real parser when `BBPPAIRINGS_BIN` is set.

## Dev seed

[`scripts/seed_brackets.py`](../../scripts/seed_brackets.py) creates, per tenant, a
"Bracket Demo" tournament per format in a mid-play state, a **DRAFT** stage still
being authored (with unrostered `TournamentPlayers` so the admin's roster import
and link-user controls have something to act on), a **CANCELLED** stage part-played
and then abandoned, plus a two-stage
groups→playoff tournament mid-chain. It drives the real `BracketService`, so the
persisted graph is internally consistent, and leaves the live states the card
renderer needs (a game in progress, one awaiting confirmation, an unbooked
matchup, a Bo3 at 1-0). Demos live on their own tournaments, never the
Challonge-mirrored one — the exclusivity guard forbids it.
