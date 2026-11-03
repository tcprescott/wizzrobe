# ALTTP item tracker

A crew-side item tracker for A Link to the Past randomizer restreams. A third
party (the match's approved tracker, staff, or a Broadcast Tech) marks what the
runner finds; viewers see it through a transparent OBS overlay, held back by the
stream delay. The runner never uses it. Behind `FeatureFlag.ITEM_TRACKER`
(category Operations, ships dark).

## Who and where

| Surface | Route | Who |
|---|---|---|
| Tracker page | `/t/<slug>/tracker/{match_id}` (`@protected_page`) | `Role.STAFF`, `Role.STREAM_MANAGER` (Broadcast Tech), or an **approved** `Tracker` on that match |
| OBS overlay | `/t/<slug>/tracker/overlay/{token}` (`@public_page`) | Anyone holding the board's unguessable `overlay_token`; `?dungeons=0` hides the dungeon row |
| Match sheet | the **Item tracker** button in the sheet headline | Shown only when `ItemTrackerService.sheet_link` returns a path: flag live, ALTTP match, viewer can track |

A match is ALTTP when its rolled seed's `randomizer` is `alttpr` or `avianart`,
or (before a roll) when its tournament's preset or `seed_generator` is one of
those.

## The page (layout C)

Board, an **On stream** preview, and a **Changes** log with per-line Undo. One
board per runner, picked with the runner toggle (`?runner=<user id>`). Tapping a
plain item toggles it; a levelled item (sword, gloves, bottles…) or a dungeon
opens a picker, a bottom sheet on phone. The dungeon picker holds boss, items
left, small keys, big key, prize and (MM/TR) medallion, and stays open while
you work through it. The header carries Undo and an Actions menu: Copy OBS
link, Stream delay…, Dungeon items…, Reset board….

Open boards poll every second (`PageTimer`) and repaint in place, so two
trackers on one match see each other's marks.

## Data

`ItemTrackerBoard` (one per match × runner) holds `state`, a flat
`{key: level}` dict (`bow`, `sword`, `ep.boss`, `ep.chests`, `ep.keys`,
`ep.big_key`, `ep.prize`, `mm.medallion`…), `delay_seconds`, an optional
`dungeon_items` override and `version`, bumped on every write so the poll
compares one integer. `ItemTrackerChange` is the log: `key`, `before`, `after`,
`actor`, `undone`, `is_undo`. Undo is allowed only on the newest change for its
key, and writes its own change (`is_undo=True`).

The stream copy is the current state rolled back by every change newer than
`now - delay_seconds` (`rewind`). Delay and the dungeon-item mode apply to every
runner on the match. Marks are not audited: the change log is their record, and
a run produces hundreds.

## Dungeon items and keysanity

The mode comes from the rolled seed's `settings_snapshot` (`dungeon_items`
`standard`/`mc`/`mcs`/`full`, or the customizer's `region.wild*` flags), and
staff can override it per match. A customizer mix that matches no preset rounds
up to the next one (big keys wild means `full`), so its item counts can run a
couple high; the override fixes that. Keysanity tracks keys only: a small-key
counter and a big-key toggle per dungeon. Maps and compasses are counted in
"items left" when shuffled, never tracked.

The catalogue (items, levels, sprites, dungeons, chest totals) is pure data in
`application/services/item_tracker_catalog.py`.

## Sprites

`static/item_tracker/` is the OpenTracker sprite set (MIT, licence file beside
it), committed with Thomas's OK. Nothing is ripped from a ROM.

## Dev seed

`scripts/seed_item_tracker.py`: "ALTTPR Tracker Demo" in the default community,
a full-keysanity seed, player_three approved as tracker, player_one's board
mid-run with one logged change. Its overlay token is stable, which is how
`scripts/ui_a11y_ci.json` names the overlay page.

## Later

A box for the OBS layouts editor (draft PR #722) can read
`ItemTrackerService.overlay(token)`.
