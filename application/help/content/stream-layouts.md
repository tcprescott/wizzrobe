---
title: Stream layouts for OBS
slug: stream-layouts
icon: picture_in_picture
order: 248
feature: STREAM_LAYOUTS
roles: STAFF, STREAM_MANAGER
summary: Designing an event's broadcast overlays, giving each stage its OBS link, and pinning a match to a stage's overlay.
---

:::snippet stream-layouts
Admin → Stream Layouts builds the overlay that sits on top of your stream in
OBS. Wizzrobe fills in everything it knows, like player names, the round and
who's on commentary. OBS keeps the video. Staff and Broadcast Techs can edit
layouts and pin matches.
:::

## How it fits together

A **layout** is your background art plus boxes placed on it. Each box shows one
thing: a player's name, the tournament and round, the commentators, the seed
code, what's up next, or your own text. A **Video hole** box cuts the art away
so the video underneath shows through.

Each **stage** gets its own overlay link and picks one layout. The overlay
follows the stage: it shows the match being played there, or the next one
booked, and moves on by itself when the next match is checked in.

## Making a layout

1. Press **New layout** and pick a starter. The SpeedGaming ones (2P 4:3
   with standard, small or DK trackers, 2P with cams only, and Zelda 1
   Randomizer) and the FFR ones (1P to 4P, and 4P co-op) have the same box
   positions as the SGL broadcast layouts, so the cams and comms line up from
   one to the next. There's also Blank, Commentary / interview and Starting
   soon / break. Each comes on a plain background colour (your community's
   colour to start) with the video holes and boxes already placed. Pick a
   colour, give it a name and press **Create**.
2. Want your own art instead? Open the layout's ⋮ and choose **Upload art…**.
   Export it at your stream's resolution (usually 1920×1080). Transparent
   areas in a PNG become holes for the video. If your art is a flat image, add
   **Video hole** boxes where the feeds go. **Plain background…** on the same
   menu swaps any art back for a single colour.
3. Press **Add box…** and pick what it shows. Drag the box into place and pull
   its bottom-right corner to resize it. Arrow keys nudge a selected box by one
   pixel, or ten with Shift.
4. Tap a box to open its settings: what it shows, which player, font, size,
   colour, alignment, uppercase and shadow. Long names shrink to fit their box.
5. Pick a match under **Preview** to see the layout with real names, then
   press **Save**. Overlays already open in OBS pick up the change within a few
   seconds.

## Putting it in OBS

On the Stages list, choose the stage's layout and press **Copy OBS link**. In
OBS, add a **Browser** source with that link, set its width and height to the
layout's size (shown under its name), and put it **above** your video sources.
Then line the video sources up with the holes.

The link opens nothing but that stage's overlay, so it's safe to hand to a
broadcaster. If it ends up somewhere it shouldn't, open the stage's ⋮ and
choose **New link…**. The old link stops working straight away.

## Pinning a match

When the overlay picks the wrong match (an overrun, or a match moved between
stages), pin the right one. Open the match from the Match Schedule and press
**Pin to overlay**, or use **Pin a match…** in the stage's ⋮ menu. The overlay
holds that match until you choose **Back to automatic** on the same menu.
