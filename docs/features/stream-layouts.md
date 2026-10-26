# Stream layouts (OBS overlays)

Wizzrobe renders a broadcast overlay for each stage that OBS loads as a
**Browser source above its video sources**. The overlay is transparent wherever
the art is transparent or a **Video hole** box cuts it, so the feeds underneath
show through with a pixel-exact edge. Everything Wizzrobe knows about the match
on that stage (players, round, crew, seed code, up next) is drawn on top. OBS
keeps the video; Wizzrobe never touches it.

Behind `FeatureFlag.STREAM_LAYOUTS` (Operations, ships dark). Staff and
Broadcast Techs (`Role.STREAM_MANAGER`) manage it. Layouts belong to an
**event** (`StreamLayout.event`); stage overlays carry their stage's event, and the Stages panel lists the current event's stages.

## Decisions (Thomas, 2026-10-26)

| Question | Pick |
|---|---|
| How layouts are designed | Upload art, then drag data boxes onto it in a Wizzrobe editor |
| Which match an overlay shows | Follows the stage, with a manual pin |
| Fields | Everything Wizzrobe has |
| Timers | None: OBS/LiveSplit own the clock |
| Who edits and pins | Staff + Broadcast Tech |
| Gate | Per-community feature flag; layouts per event |
| Flat art (no alpha) | Editor can cut Video hole boxes into it |
| Editor layout | B: art full width, box list below, settings in a side sheet (bottom sheet on phone) |
| Starter layouts | A: a gallery in the New layout dialog, plain background colour (community header colour first) |

## Models

`models/stream_layout.py`, migration `137_*_stream_layouts.py`.

| Model | What |
|---|---|
| `StreamLayout` | `tenant`, `event`, `name` (unique per event), `width`/`height` (the art's size), `boxes` (JSON list), `art_sha256` |
| `StreamLayoutArt` | One row per layout: the art as a PNG this app encoded (`bytea`), keyed by `sha256` |
| `StageOverlay` | One per stage: `layout` (nullable), `token` (the secret in the OBS URL), `pinned_match`, `pinned_by`, `pinned_at` |

`boxes` is validated on every write by `application/services/stream_layout_rules.py`
(`clean_boxes`): two kinds, `field` (one of `FIELDS`, with `which` player 1–4,
`font` from an allowlist of stacks, `size`, `weight` 400/600/800, `color`
`#RRGGBB`, `align`, `upper`, `shadow`, custom `text` ≤ 200 chars) and `hole`
(`radius`). Every box is clamped onto the canvas. At most 60 boxes.

## Starters

`application/services/stream_layout_starters.py` (pure data): thirteen
`Starter`s at 1920×1080 in three gallery groups. **SpeedGaming** and **FFR**
copy the box positions (never the art) of the SGL broadcast layouts Thomas
shared from the layout designers (2026-10-26): `sg_2p_trackers`,
`sg_2p_small_trackers`, `sg_2p_dk_trackers`, `sg_2p_cams`, `sg_z1r`, `ffr_1p` to
`ffr_4p`, `ffr_4p_coop`. **Basic** is `blank`, `commentary`, `break`. Each is a
tuple of boxes, plus `BACKGROUNDS` and `plain_background`
(a solid RGBA PNG). `create_layout(actor, name, starter=, background=)` copies
the starter's boxes through `clean_boxes` and, given a colour, stores the
plain PNG as the layout's art; the holes are hole boxes, so the overlay cuts
them like any other. `set_background` swaps a layout's art for a plain colour
at its current size. A starter is only a first draft: nothing links a layout
back to the starter it came from.

## OBS scene download

`application/services/stream_layout_obs.py` (pure) builds an OBS 28+ scene
collection: per stage with a layout, a scene of nested **feed scenes** (one per
hole `label`; an unnamed hole gets its own) under a Browser source for the
overlay, plus the feed scenes, each holding a grey `color_source_v3`
placeholder the size of the biggest hole using it. Stage scenes crop the
nested feed to that rectangle and fit it with "scale to inner bounds", so
slightly different holes share a feed. UUIDs are `uuid5` of the names, so the
same layouts produce the same file. `StreamLayoutService.obs_scene_collection`
serves it to Actions → Download OBS scenes and the stage ⋮.

## Service

`StreamLayoutService` (`@requires_feature` on every public entry). Layout CRUD
(`create_layout`, `rename_layout`, `duplicate_layout`, `delete_layout`),
`save_boxes` (refuses a stale copy by `updated_at`), `set_art` / `set_background` / `remove_art`,
`stage_overlays` (creates a stage's overlay row on first look),
`set_stage_layout`, `renew_link`, `pin_match` / `unpin` / `pin_state`,
`preview_values` / `preview_matches` for the editor, and `overlay_feed` for the
public page. Every write is audited (`stream_layout.*`, `stage_overlay.*`).

Art goes through `prepare_art`: PNG, JPEG or WebP, ≤ 10 MB, 320×180 to
3840 px, decoded, EXIF-rotated and re-encoded as an RGBA PNG, so only bytes
this app produced are served. New art resizes the canvas and scales the boxes.

### What an overlay shows

`_showing`: the pinned match if it still exists; otherwise, among the stage's
unfinished matches booked from 12 hours ago to 18 hours ahead, the **latest one
checked in or started**, else the **next booked** (from an hour ago on). "Up
next" is the following unstarted match on that stage. Times render on the
community's clock (`TimezoneService.tenant_timezone_name`).

Field values: `player_name` / `player_pronouns` (players in join order),
`players_all`, `tournament`, `tournament_short`, `round` (`bracket_line_for`,
or a crew hold's title), `tournament_round`, `match_title`, `scheduled_time`,
`seed_code`, `prize_pool` (pool + bonus), `stage_name`, `channel`
(`twitch.tv/<channel>` from the stage's stream URL), `commentators` / `trackers`
(approved only), `up_next`, `event_name`, `community_name`, `custom_text`.

The feed is cached per overlay for 2 s, and any layout or pin write clears it.

## Public routes

`pages/stream_overlay.py`, registered in `frontend.py`. Plain FastAPI, no
NiceGUI: an OBS source stays open for days, and a page with no websocket can't
drop one mid-show.

| Route | What |
|---|---|
| `/t/<slug>/overlay/<token>` | The overlay page. Transparent body; art on a canvas with holes cut by `destination-out`; text via `textContent`, shrunk to fit its box. Polls `feed` every 2 s and rebuilds only when the layout version changes |
| `/t/<slug>/overlay/<token>/feed` | JSON: `version`, size, `art_url`, `boxes`, `values` |
| `/t/<slug>/overlay-art/<layout>/<sha>.png` | The art, immutable by hash |

The token (`ovl_` + 24 random bytes) is stored as is, because Broadcast Techs
copy the link again and again; it opens only that stage's broadcast data, which
is what the Watch page shows anyone. **New link…** replaces it. A token from
another community 404s (the overlay's tenant must be the request's). All
responses are `no-store` and `noindex` except the art.

## Surfaces

- **Admin → Stream Layouts** (`pages/admin_tabs/admin_stream_layouts.py`,
  Operations, Staff or Broadcast Tech, flag live). Stages panel: layout select,
  what's showing, **Copy OBS link**, ⋮ Pin a match… / Back to automatic / Open
  overlay / New link…. Layouts panel: Edit, ⋮ Rename… / Duplicate / Delete….
- **The editor** swaps in on Edit. Dragging, resizing (bottom-right handle)
  and arrow-key nudging are `static/js/stream-layout-editor.js`, which emits
  `sl_box_changed` / `sl_box_selected`. Styles in `static/css/stream-layouts.css`.
- **Match sheet** on Admin → Match Schedule: **Pin to overlay** for a match on a
  stage, when `MatchBoardAccess.pin_overlay` (Staff or Broadcast Tech, flag live).

No REST router or MCP tool exists yet.

## Not built

- A broadcaster crew role's name field: add a `broadcasters` field once the
  "Add a broadcaster to each match" work lands.
- Image boxes (logos, flags) beyond the background art.
