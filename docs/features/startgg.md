# start.gg brackets

A tournament can follow an event on start.gg instead of a native or Challonge
bracket. Wizzrobe mirrors the event read-only (phases, pools, sets, entrants and
standings), lets the players of a ready set book it from My Schedule, and can
optionally report confirmed results back. start.gg stays the source of truth:
the event's admins record results there, and every sync overwrites the mirror.

Behind `FeatureFlag.STARTGG`. A tournament follows at most one bracket site:
linking start.gg refuses a Challonge-linked tournament or one with a native
bracket, and the other two refuse a start.gg-linked one.

## Key files

| File | Role |
|---|---|
| `application/utils/clients/startgg_client.py` | GraphQL client (`https://api.start.gg/gql/alpha`, Bearer PAT), `parse_event_slug`, the OAuth identity client. Reads are split to stay under 1,000 objects per request; 80 requests a minute is the API's limit. |
| `application/utils/mocks/mock_startgg.py` | `MOCK_STARTGG`: one canned event (two finished round-robin pools feeding a four-player final bracket). |
| `models/startgg.py` | `StartggEntrant`, `StartggPhase`, `StartggPool`, `StartggSet` and `StartggSetState`; `Tournament.startgg_*`, `User.startgg_*`. See [data-model.md](../reference/data-model.md). |
| `application/repositories/startgg_repository.py` | Tenant-scoped mirror reads and writes, prune, `sets_for_match`. |
| `application/services/startgg_service.py` | `StartggService`: link, sync, entrant mapping, booking, the public view, writeback. |
| `application/services/startgg_connection_service.py` | `StartggConnectionService`: the community's start.gg sign-in (`StartggConnection`), token refresh, which token a call uses, and the per-tournament access check. |
| `pages/admin_tabs/admin_startgg.py`, `pages/startgg_oauth.py` | The start.gg card of Settings → Integrations; `/startgg/connect` and the shared OAuth callback. |
| `application/services/startgg_link_service.py` | `StartggLinkService`: players link their start.gg account on Profile (`user.identity`). |
| `application/services/bracket_sources.py` | The shared contract every external bracket site plugs into (below). |
| `application/services/startgg_sync_worker.py` | Re-syncs every linked event every 2 minutes. |
| `pages/startgg_bracket.py`, `static/css/startgg.css` | Public bracket page `/tournament/{id}/startgg`. |
| `theme/dialog/startgg_section.py`, `theme/dialog/startgg_entrants_dialog.py` | Edit tournament → Integrations: link, bookable phase, writeback opt-in, Sync now, Entrants. |
| `theme/dialog/bracket_source_schedule_dialog.py` | The My Schedule picker for a ready set. |
| `scripts/seed_startgg.py` | Dev seed: "OoTR Singles (start.gg)" linked to the mock event. |

## Configuration

`STARTGG_CLIENT_ID` / `STARTGG_CLIENT_SECRET` (a start.gg OAuth app) turn on
both the player profile link and the staff **community sign-in**; both use the
one redirect URI `{BASE_URL}/startgg/oauth/callback`, and the pending
`startgg_connect_state` tells the callback a sign-in is the staff flow.
`STARTGG_API_TOKEN` (a Personal Access Token) is optional: a community with no
sign-in syncs and reports with it. See the env table in
[deployment.md](../deployment.md).

### The community sign-in

Staff press **Connect start.gg** on Settings → Integrations and sign in with
`user.identity tournament.reporter`. The grant is one `StartggConnection` row
per community; every start.gg call in that community (syncs, the worker,
writeback) uses it. Access tokens last 7 days and refresh a day before they
lapse, under a per-community lock because start.gg may rotate the refresh
token. A refresh that fails falls back to `STARTGG_API_TOKEN` (or the stale
token) and the card says to reconnect.

Reporting needs the signed-in account to hold an admin role, **Reporter** or
higher, on the start.gg *tournament* (its Settings → Admin Permissions). The
card checks each linked tournament: yes if start.gg lists it among the
account's admin-view tournaments or lists the account among its owner/admins;
"Read only" when both answer no; "Couldn't check" when neither answers.

## Mapping entrants to members

A start.gg entrant becomes a Wizzrobe player in this order: a staff assignment
(`user_assigned_manually`, survives every sync), the member who linked that
start.gg account on Profile, the member whose Discord id start.gg reports for
the entrant. Failing all three, a unique same-name community member is stored
as a **suggestion** only, which staff confirm or dismiss in the Entrants
dialog. Only community members are ever mapped. Mapped players are mirrored
into `TournamentPlayers`, so a start.gg tournament takes sign-ups on start.gg
and the Tournaments tab says so.

## Booking and DMs

Only sets in the **bookable phase** can be booked (by default the earliest phase start.gg
has under way, else the last phase; staff can pin one under Integrations). A set is bookable when it is OPEN on
start.gg, is a real set (not a `preview_` id), has both entrants mapped and has
no match yet. A sync that makes a set newly bookable DMs both players with a
link straight to the picker (`/home/player?startgg=<set id>`); booking goes
through `MatchService.submit_match_request(from_bracket=True)` and links the
match to the set.

## Best-of sets

A stage the round schedule marks best of 3 or 5 (`Tournament.config
['round_schedule'][key]['best_of']`, Admin → Tournaments ⋮ → Round schedule) is
booked as one `Match` per game, for Challonge and start.gg alike
(`BracketSetService`). One game leads the set: it is the booking the bracket row
links to, and the others carry `Match.set_first` pointing at it, with
`set_best_of` on every game.

Players book a set a game at a time and in any order. My Schedule's "Waiting on
you to pick a time" lists one row per game left to book, unnumbered ("best of 3
· another game", the games past a sweep tagged "If needed";
`BracketSetService.open_games`, `bracket_sources.set_game_label`), and each row
books one game (`bracket_sources.book_set_game` → `BracketSetService.book_game`).
The first game booked takes the bracket link and leads the set. Staff booking a
matchup nobody has booked yet still get the dialog that books every game at once
(`bracket_sources.schedule_set`, prefilled back to back at match length + 10
minutes, best-of changeable per matchup); once a game is in, Match Schedule's
Not booked yet shows the set as "2 games left" and books the rest one at a time.

"Game 2 of 3" isn't stored: it is the game's place on the clock among the set's
games (`application/utils/game_order.py`): started games first in the order they
started, then the rest by booked time. Moving a game moves its number, and a
started game's number never changes. `set_game` only records booking order.
`BracketSetService.labels_for` fills the run sheets in one query; DMs use
`label_for`. Native bracket games number the same way
(`BracketService.numbers_by_time`), though their `game_number` slot stays the
booking's identity.

The bracket site hears only the set's result, once a player has won a majority,
with the game score (Challonge `score_set` 2–1); the unplayed games are then
called off as the system user (`BracketSetService.call_off_unneeded`, run from
`bracket_sources.on_match_confirmed`), and a decided set stops offering games.
Calling off or deleting the game that leads the set hands the lead and the
bracket link to its earliest other game first (`BracketSetService.hand_off`), so
the rest stay booked; calling off a set's only game frees the matchup.
Placeholder fills reach every game.

## Writeback

Off by default, per tournament ("Report confirmed results to start.gg").
When a linked match is confirmed, `report_result_if_linked` re-reads the set
from start.gg and reports the winner with `reportBracketSet` only if start.gg
has no result yet; it never overwrites. Each report is audited
(`startgg.result_reported`). It is a soft integration point: any failure is
logged and swallowed so match confirmation never breaks.

## The bracket-source contract

`application/services/bracket_sources.py` is the one place callers touch "a
linked bracket" without naming a site. Each site is a `BracketSource`
(`key`, `label`, `flag`, `linked`, `sync`, `list_bookable_for_user`,
`schedule`, `on_match_confirmed`):

- match confirmation calls `bracket_sources.on_match_confirmed` (each site's
  failure is isolated),
- My Schedule lists `list_bookable_for_user` and books through `schedule`,
- Admin → Tournaments' row **Sync now** (under the row ⋮'s *start.gg bracket*
  group) and Actions → **Sync all linked
  brackets** use `sync_tournament` / `sync_all`,
- **Booking ahead** (Challonge too): staff and tournament admins book a later elimination matchup from Not booked yet › Players TBD before its players are known (`book_ahead`, `_startgg_placeholders.py` / `_challonge_placeholders.py`). The match carries `bracket_placeholders`; each sync fills players in and DMs them the usual acknowledgment request. Preview sets of an unstarted phase can't be booked. A reset flags the booking `bracket_link_lost`; the TBD row's ⋮ re-links it (`relink_ahead`).
- Match Schedule's **Not booked yet** view lists `list_unbooked(actor)` (every
  site's open, unbooked matchups plus native brackets', scoped to staff or the
  actor's administered tournaments) and books through `schedule`. Staff and
  tournament admins book for the players via `MatchService.book_from_bracket`,
  which routes them through `create_match`; `allow_outside_round` carries their
  "Book anyway".

start.gg implements all of it. Challonge implements `sync` and `schedule`; its
result push and My Schedule rows still run through `ChallongeService` until they
move onto the contract. Native brackets live in Wizzrobe and have nothing to sync.
