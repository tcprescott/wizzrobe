---
title: Presets and your own preset space
slug: presets
icon: casino
order: 44
summary: Browsing the preset library, rolling a seed from any preset, and keeping presets of your own with friends who can edit them.
---

A preset is a named set of randomizer settings. Every preset has a full name made
of two parts, like `sgl/sgl2026` or `tom/fastboots`. The part before the slash is
the space it lives in. A community's presets live in its own space, and each
player can have a space of their own.

## Browsing and rolling

Open **Me › Presets** in the app. The list shows every preset a community has
shared plus every player's presets. Use the search box, the game chips and the
switch at the top to narrow it down. Pick one to read its settings.

**Roll a seed** rolls one straight away and shows the link and the file-select
code. Rolls use this community's randomizer keys, so if a game shows a warning
instead, this community hasn't set that randomizer up yet. You can roll 10 seeds
every 10 minutes.

### Rolling in a race room

In a racetime.gg room Wizzrobe's bot is in, type `!roll` and the full name, like
`!roll wizzrobe/open` or `!roll tom/fastboots`. The bot posts the seed link and
code for everyone in the room. A name without a slash, like `!roll sgl2026`,
rolls from this community's own presets. Link your racetime.gg account under
**Me › Profile** first so the bot knows who you are. Room rolls count towards the
same 10 every 10 minutes.

## Wizzrobe's own presets

The `wizzrobe` space holds the presets that ship with Wizzrobe, including every
global preset SahasrahBot had (`wizzrobe/open`, `wizzrobe/casualboots`,
`wizzrobe/crosskeys` and so on). Nobody edits them in the app. Roll them as they
are, or copy one to your own space to change it.

## Your own presets

Press **New preset**, pick the game, give it a short name and paste in the
settings. Your space is made the first time you save, named after you. Names use
lowercase letters, numbers, `-` and `_`, up to 32 characters, so they're easy to
type in a race room.

Everything in your space shows in the library for everyone to read and roll.
Don't keep race-day secrets there.

To start from someone else's preset, open it and choose **Copy to my presets**
under Actions. Your copy remembers where it came from. If the original's settings
change later, your copy says so and offers **Update copy**. It never changes on
its own.

A space holds 50 presets.

## Mystery presets

A mystery preset (game **A Link to the Past (mystery)**) holds a weightset
instead of settings: each setting with the odds of each value, in SahasrahBot's
format. Every roll picks one value per setting, so nobody knows the mode until
they play it. The seed page and the match don't show what it picked. Players
only see the weightset's name and any instructions it hands out.

Open one to see its odds. To write your own, start from one of the `wizzrobe`
weightsets (`wizzrobe/friendly` is a good first one): copy it to your presets,
then **Edit** and change the YAML. **Check odds** rolls it a few times to make
sure it works and shows the chance of each value. A weightset can also pick a
whole preset by name with `preset:`, from Wizzrobe's own ALTTPR and door presets.

## Bringing your SahasrahBot presets over

If you kept presets on SahasrahBot, choose **Actions › Import from SahasrahBot**
and type your SahasrahBot namespace (usually your Discord name). Wizzrobe lists
your ALTTPR, ALTTPR mystery, Super Metroid and SMZ3 presets. Tick the ones you want, up to 25 at
a time, and they land in your own space. Ones you already have are marked.

Door presets come over as Door Randomizer presets, with the spoiler for staff
only. Presets that place items themselves (`forced_locations`) can't come over
yet, so those show the reason instead of importing.

## Letting friends edit

Open **Actions › My preset space** to rename your space or add co-editors. A
co-editor is any member of this community. They can add, edit and delete presets
in your space, but only you can rename it or change who edits it. You can have
10 co-editors. A co-editor can leave from the same screen.

## Using a preset in a tournament

A tournament only rolls from its own community's presets. If you'd like one of
yours used, ask the community's staff. They copy it into Seed Presets and attach
the copy to the tournament, so your later edits never change a match that's
already set up.
