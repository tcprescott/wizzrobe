---
title: Running group races
slug: staff-group-races
icon: directions_run
order: 247
feature: RACETIME_ROOMS
roles: STAFF, QUALIFIER_ADMIN
summary: Dailies, weeklies and qualifier live races the bot opens on its own, posts in Discord, seeds and locks.
---

A group race has no player list. Anyone can join its racetime room. The bot opens
the room before the start, posts it in your Discord channel, posts the seed and,
if you want, locks the room so nobody joins late. When the race finishes, everyone
who linked racetime here gets their place and time on the race. Anyone who hasn't
linked is listed by their racetime name.

All of this happens in the Wizzrobe app, not on the website.

## Setting up a series

Open the tournament under Admin → Tournaments and pick the **Racetime** tab.

- Pick the **Racetime bot** and, if you use one, a **Room profile**.
- Set **Race type** to **Group race**. The bot then opens and runs every room
  itself.
- **Announce in** is the Discord channel the room is posted in, and **Ping role**
  is the role pinged with it. Leave the channel empty to skip Discord.
- **Room opens** and **Seed posts** are minutes before the start.
- Turn on **Lock the room when the seed posts** for races where nobody should join
  after seeing the seed. Most dailies leave it off.

The seed rolls from the preset on the tournament's Settings tab.

A qualifier's live races work the same way. The settings are on the qualifier's
Settings page under **Live races with a start time**, and the seed is the pool
permalink you pick for each race.

## Where races come from

Link the SpeedGaming event to the tournament under Admin → SpeedGaming, and every
episode becomes a race on its own. To add one yourself, open Admin → Group races
and choose **New group race**. Pick the tournament (or the qualifier pool and its
permalink), give it a title and a start time. Put "co-op" in the title to open a
team race.

## On the day

Admin → Group races lists what's coming up, by day. Open a race to see what the
bot has done so far, who's in the room, and the Discord post it sent. Anyone who
hasn't linked racetime here is marked **Not linked**, so you can nudge them before
the race ends.

The **Actions** menu covers the usual fixes:

- **Open the room now** opens it early and posts it.
- **Post the seed now** puts the seed in the room straight away.
- **Re-post announcement** posts it in Discord again, if the message was deleted
  or you changed the channel.
- **Lock the room now** stops anyone else joining.
- **Cancel race…** takes the race off the schedule. Close the room on racetime.gg
  as well.

**Finished** shows the last two weeks of races with their results.
