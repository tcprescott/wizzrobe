---
title: Seed presets and test rolls
slug: staff-presets
icon: tune
order: 244
roles: STAFF, PRESET_MANAGER
summary: Adding and importing seed presets, checking them with a test roll, and attaching them to a tournament.
---

A preset is a named set of randomizer settings. Pick one on a tournament and every
seed rolled for its matches uses it. You'll find presets on Admin → Seed Presets,
under Operations next to Tournaments. Staff and anyone with the Preset Manager role can change them.

## Getting presets in

There are three ways to add one.

- **Import → Built-in presets** adds the presets that ship with Wizzrobe. Run it
  again and it only adds the ones you don't have yet.
- **Import → From a randomizer** pulls in the presets a randomizer publishes on its
  own site. Pick the randomizer, press **Fetch presets**, then type in
  **Presets to import** to filter and pick the ones you want. What you import is
  your own copy, so later changes on the randomizer's site won't follow it.
- **Add Preset** takes a name, a randomizer, a description and the settings as
  JSON. Use it when you have the exact settings from a randomizer's own tools.

A Wind Waker preset is a single permalink instead of JSON. Set things up in the
randomizer, copy what its Permalink box shows, and paste it into **Permalink**.
Wizzrobe shows the build it's for and the main settings underneath, so you can
check it's this season's. Each roll keeps the build and settings and only changes
the seed. The SGL 2026 permalink is one of the built-ins.

A Final Fantasy preset is the flags link from finalfantasyrandomizer.com. Set the
flags on the site, copy the link from the address bar, and paste it into
**Flags link**. Wizzrobe shows the site version it pins, with an **Open on site**
link so you can check the flags there. A Zelda 1 preset is the flag string, pasted
into **Flags**, and underneath you'll see an example of what players get handed.
Neither rolls without a preset attached, and the SGL 2026 flags for both are
built-ins.

Some randomizers need your community's own API key before they show up in the list
or roll anything. Set those under Settings → Integrations → Randomizer Keys. Once
a key is saved you can only see whether it's set, never the key itself.

## Finding and changing a preset

Presets are grouped by the randomizer they roll, one line each, and every line
says how many tournaments use it. Click a preset's name to edit it. **Test roll**,
**Edit** and **Delete** are in the ⋮ menu at the end of the line.

**Delete** asks first. If any tournament rolls from that preset, it names them.
They'll lose the preset and need another one picked before they can roll.

## Test roll before the event

:::snippet admin-preset-test-roll
Every preset has a **Test roll** in the ⋮ menu at the end of its line. It rolls one
seed the way a match would and shows you the link, or the randomizer's error if the
roll failed. The seed isn't attached to a match and nobody gets sent it.
:::

![The Test roll dialog after a successful roll, with the seed link marked 1](/static/help/staff-presets/test-roll.webp "A test roll that worked, with its seed link (1).")

Test every preset your tournaments use before doors open, and again whenever you
change its settings. Otherwise you'll find the failure with two players sitting at
a station.

Most rolls take a few seconds. Donkey Kong 64 can take a few minutes, so leave the
dialog open.

A few randomizers roll their own bundled settings and ignore the preset's. The
dialog tells you when that's the case. A good roll then proves the randomizer
works, but says nothing about this preset's settings.

## Attaching a preset to a tournament

Open the tournament on Admin → Tournaments and pick it under Seeds & randomizer →
**Seed Preset**. The preset decides which randomizer the tournament rolls with, so
there's nothing else to set. Pick **None (no seeds)** and the tournament doesn't
roll seeds.

A tournament set up before presets existed may say it's still rolling with an
older generator and no preset. Pick a preset to replace it. Saving with None turns
seed rolling off.

## The harder preset

**Harder Preset (opt-in)** is a second preset players can ask for privately, one
match at a time. A match only rolls it when every player in it opted in. Nobody
finds out whether their opponent opted in unless they both did, so nobody feels
dared into it.

Both presets have to use the same randomizer. Test roll the harder one too.

## Rolling a match's seed

Once a tournament has a preset, open a match on Admin → Match Schedule or the
Proctor Station. The Seed line of its checklist offers **Generate** when the match
has players, has no seed yet, isn't finished and doesn't run in a racetime.gg room.
Rolling DMs the seed to the players. Staff, proctors and the tournament's admins
can roll.
