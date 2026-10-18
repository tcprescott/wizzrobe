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
| `application/services/startgg_link_service.py` | `StartggLinkService`: players link their start.gg account on Profile (`user.identity`). |
| `application/services/bracket_sources.py` | The shared contract every external bracket site plugs into (below). |
| `application/services/startgg_sync_worker.py` | Re-syncs every linked event every 2 minutes. |
| `pages/startgg_bracket.py`, `static/css/startgg.css` | Public bracket page `/tournament/{id}/startgg`. |
| `theme/dialog/startgg_section.py`, `theme/dialog/startgg_entrants_dialog.py` | Edit tournament → Integrations: link, bookable phase, writeback opt-in, Sync now, Entrants. |
| `theme/dialog/bracket_source_schedule_dialog.py` | The My Schedule picker for a ready set. |
| `scripts/seed_startgg.py` | Dev seed: "OoTR Singles (start.gg)" linked to the mock event. |

## Configuration

`STARTGG_API_TOKEN` (a start.gg Personal Access Token) turns the mirror on;
`STARTGG_CLIENT_ID` / `STARTGG_CLIENT_SECRET` turn on the profile link, whose
callback is `{BASE_URL}/startgg/oauth/callback`. See the env table in
[deployment.md](../deployment.md). Writeback needs the token's account to be a
Reporter (or admin) on the start.gg event.

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

Only sets in the **bookable phase** can be booked (the last phase by default;
staff pick another under Integrations). A set is bookable when it is OPEN on
start.gg, is a real set (not a `preview_` id), has both entrants mapped and has
no match yet. A sync that makes a set newly bookable DMs both players with a
link straight to the picker (`/home/player?startgg=<set id>`); booking goes
through `MatchService.submit_match_request(from_bracket=True)` and links the
match to the set.

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
- Admin → Tournaments' row **Sync bracket** and Actions → **Sync all linked
  brackets** use `sync_tournament` / `sync_all`,
- Match Schedule's **Not booked yet** view lists `list_unbooked(actor)` (every
  site's open, unbooked matchups plus native brackets', scoped to staff or the
  actor's administered tournaments) and books through `schedule`. Staff and
  tournament admins book for the players via `MatchService.book_from_bracket`,
  which routes them through `create_match`; `allow_outside_round` carries their
  "Book anyway".

start.gg implements all of it. Challonge implements `sync` and `schedule`; its
result push and My Schedule rows still run through `ChallongeService` until they
move onto the contract. Native brackets live in Wizzrobe and have nothing to sync.
