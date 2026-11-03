# Prize payouts

Behind `FeatureFlag.PAYOUTS`, off by default. Prize money is SpeedGaming-shaped;
most communities never pay anyone.

Records what each placement of a tournament wins, and produces the block that
used to be typed by hand into an admin thread after every event.

## The model

| Where | What |
|---|---|
| `Tournament.prize_pool` | The advertised pool. `NULL` until someone decides it |
| `Tournament.prize_bonus` | The leaderboard bonus, added to the pool before shares apply |
| `Tournament.sglive_slug` / `prize_pool_synced_at` | The game's sglive.speedgaming.org slug (`wwr`) while the pool syncs from there, and when it last did |
| `TournamentPayout` | One placement's share: `place`, exactly one of `percentage` or a fixed `amount`, optional `entrant` and `note` |
| `User.matcherino_username` | Where the money is sent. Global, self-entered, **not** unique: under a unique constraint someone else's typo would lock the rightful owner out of their own handle. The numeric `matcherino_user_id` is unique because it is only written from evidence (an OAuth-verified id match or a desk-confirmed link) |

Two properties of `TournamentPayout` carry most of the design:

**Percentage amounts are computed, never stored.** A percentage row's money is `(prize_pool + prize_bonus) × percentage / 100`, quantized to the
cent, worked out every time it is read (`PayoutService.amount_for`). The pool
moves throughout an event, and a stored amount would be a second source of truth
that drifts the moment it does. Rounding is per row, so a split whose
percentages sum to exactly 100 can total a cent under the pool — every line pays
what it says it pays, and the remainder is the organizer's to place.

**Fixed amounts are for announced dollars.** Some organizers promise "1st gets
$650" rather than a share. $650 of $1,490 is 43.6241…%, which a two-decimal
percentage can't hold, so a row may store `amount` instead (SGL26 ALTTPR was the
case). A fixed row ignores the pool: it pays its amount whatever the pool does,
and a split may mix fixed and percentage rows: the Split dialog has a $ | %
switch on every row.

**Ties are first-class.** The unique key is `(tournament, place, entrant)`, not
`(tournament, place)`. SGL's ALTTPR 2025 paid two third places at 10% each;
halving one 20% third place would hide the arithmetic from the admin checking
it. Postgres treats `NULL`s as distinct, so several unnamed rows can sit at the
same place while the split is drafted, and once both are named the key stops one
person being paid twice at one place.

## Who may edit

`AuthService.can_manage_payouts(user, tournament)` — staff, a super-admin, or
that tournament's admin. Reads are gated with the writes: an unannounced split
is admin data, so a plain member gets a 403 rather than a preview of who is
being paid what. A tournament admin sees only their own events; the Payouts tab
lists exactly what `PayoutService.list_overview` returns, so it never offers a
row whose controls would refuse.

## The service

`application/services/payout_service.py`. Every public method carries
`@requires_feature(FeatureFlag.PAYOUTS)`.

| Method | Does |
|---|---|
| `list_overview(actor)` | Every tournament the actor may manage, with pool, total, place count and whether every place is named |
| `get_split(tournament_id, actor)` | The rows, with amounts |
| `set_pool(tournament_id, pool, bonus, actor)` | The two `Tournament` columns. `None` clears a value, which is not the same as zero |
| `set_split(tournament_id, rows, actor)` | Replaces the whole split in one transaction |
| `set_entrant(payout_id, user_id, actor)` | Names a winner on a drafted row |
| `export_block(tournament_id, actor)` | The text to paste |

Validation raises `ValueError`: each row has exactly one of percentage or
amount, shares may not sum above 100 (below is fine — an unallocated remainder is
real), with any fixed row the money may not sum above the total (skipped while no
pool is set, so dollars can be drafted early), a place is at least 1, a share or
amount is above 0, and neither pool nor bonus may be negative.

`set_pool` and `set_split` / `set_entrant` audit and publish through
`AuditService.write_and_publish` as `tournament.prize_pool_updated` and
`tournament.payout_updated`. `get_split` and `export_block` deliberately publish
nothing: pasting a block into a thread changes no state.

## The export

```
Total prize pool: $1000.00
Bonus: $100.00

1st place - Jemgold (jemgold#100234): 50% / $550.00
2nd place - blueshell (blueshell#204871): 30% / $330.00
3rd place - greenpotion (no Matcherino handle): 10% / $110.00
3rd place - (unassigned): 10% / $110.00
```

A fixed row drops the percentage and reads `1st place - Jemgold (jemgold#100234): $650.00`.
A row with no entrant reads `(unassigned)`; an entrant with no handle is called
out rather than left blank, because a silently missing handle is what stalls a
payout run and the reader of the block is the person who can go and ask for it.

## Syncing from sglive.speedgaming.org

SpeedGaming Live's site sorts Matcherino contributions into per-game pools and
serves them as JSON at `https://sglive.speedgaming.org/data/` (`tournaments[]`
with `slug`, `short_name`, `donations_total` as `"$1,054.00"`, and the donor
list). `application/utils/clients/sglive_client.py` reads it; a payload with no
games raises `SgliveAPIError` instead of returning an empty list.

| Piece | |
|---|---|
| `PayoutService.set_sglive_links(links, actor)` | `{slug: tournament_id or None}` from the link screen; one slug per tournament; audits `tournament.sglive_link_updated` per change, then fills pools at once |
| `PayoutService.unlink_sglive` | Clears the slug; the last synced amount stays |
| `PayoutService.apply_sglive_games(games, actor)` | Writes totals into linked pools; a moved pool audits `tournament.prize_pool_updated` with `source: sglive`, an unchanged one only stamps `prize_pool_synced_at`. Returns linked slugs sglive no longer lists |
| `PayoutService.set_pool` | Refuses a *different* pool while linked (the bonus is still editable) |
| `sglive_pool_worker` | Every 15 min: idle with nothing linked, else one fetch fanned out per tenant as the system user, skipping tenants with `PAYOUTS` off. First failure ERROR, repeats WARNING; pools keep their last value |

The event info `:::tournaments` list shows pool + bonus as a chip when
`PAYOUTS` is on and the total is above zero, with a footnote naming the last
sync time when any row syncs. The Watch page's Tournaments list
(`pages/watch_data.py`, both renderers in `theme/watch_view.py` /
`theme/watch_live.py`) reuses `event_info_service.prize_fields` for the same
chip, with a footnote naming sglive when any row syncs. A pool change publishes a
`tournament.*` event, which invalidates the cached Watch view, so the static page
picks it up on its next in-place refresh.

## Surfaces

| Surface | |
|---|---|
| Admin → Payouts (`pages/admin_tabs/admin_payouts.py`) | The tournament table, the pool and split dialogs, **Copy for Matcherino**, and Actions › **Link sglive pages** / **Update from sglive now** |
| Event info `:::tournaments` (`theme/event_info_tournaments.py`) | Public pool chip per tournament |
| Watch page Tournaments list (`theme/watch_view.py`, `theme/watch_live.py`) | The same public chip, live and static |
| Profile → Matcherino handle (`pages/home_tabs/player_edit_info.py`) | Its own card, deliberately apart from the OAuth-verified Challonge/Twitch/racetime rows so it never reads as verified. Hidden where the community lacks the feature |
| `GET/PUT /api/tournaments/{id}/payouts`, `PUT /api/tournaments/{id}/prize-pool` | Money serialises as strings |
| MCP `get_tournament_payouts` | Read only. The MCP write surface is opt-in at consent, and prize money is not where to extend it first |
