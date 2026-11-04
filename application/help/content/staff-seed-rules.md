---
title: Seed rules
slug: staff-seed-rules
icon: casino
order: 245
audience: staff
summary: Rolling different presets by round, by game in a set, by match title or stage, or by a weighted draw.
---

A tournament rolls its own preset for every match unless you tell it otherwise.
Seed rules are how you tell it otherwise: Open for the early rounds, Standard for
the top 8 decider, a weighted mystery for the grand final. You'll find them on
Admin → Tournaments → a tournament → **Seed rules**. Staff, preset managers and the
tournament's own admins can change them.

## How a match picks its preset

Rules are tried from the top, and the first one that fits the match decides. The
**Otherwise** line at the bottom is the tournament's own preset, set on the
Settings tab, and rolls whenever no rule fits.

Two things still beat every rule. A preset staff force on one match wins, and so
does the harder preset when every player in the match opted in.

A rule whose preset has since been deleted is skipped, and the next one decides.
Seeds that are already rolled keep what they rolled when you change the rules.

## Writing a rule

Press **Add rule**, name it, and add conditions under **When**. Every condition
has to hold. Inside one condition you can pick several values and any of them
counts.

- **Round** offers the rounds of the tournament's bracket, the same ones Round
  schedule lists.
- **Game in set** picks game 1, 2, 3 and so on of a best-of set. **Decider** is
  the set's last game, whatever its number. A single game is never in a set, so
  this condition never fits one.
- **Title contains** looks for any of the words you list in the match's title,
  ignoring case.
- **Stage** fits only matches on a stage, or only matches off one.
- **Date range** fits matches scheduled from one time and before another.
- **Form answer** fits on what the players answered on the tournament's match
  form (below): every player asked, or any one of them, answered one of the
  choices you pick. Pick no choices and any answer counts. A question nobody has
  answered never fits, and "every player" needs every one of them to answer.

A rule with no conditions fits every match, so anything below it never fires.

Under **Then**, pick one preset, choose **Weighted** and give each preset a
weight, or choose **Players' answer** to roll the preset linked to the choice the
players picked on a match form question. Split answers skip the rule. A weighted rule draws one preset per match, so Open 50, Standard 30 and
Inverted 20 rolls Open about half the time. The seed records what was drawn.

**Setting tweaks** change one setting on top of the preset for this rule only,
such as `item.swords` set to `assured`. Use a dot to reach a nested setting. The
preset itself doesn't change.

Change the order from the ⋮ on the open rule (**Move up**, **Move down**), and
press **Save rules** when you're done. Nothing changes for players until you save.

## Trying it before you save

The **Preview** at the bottom of a rule tries your unsaved rules on a real match,
or on a made-up one where you pick the round, the game and the title. It names
the rule that fires and what it would roll, with the odds for a weighted draw.
It doesn't roll anything.

## The match form

The **Match form** tab beside Seed rules holds questions players answer before a
match: Yes / No, Pick one, Pick several, or a number. Players only pick from the
choices you list, and nothing is typed freely. Each question says:

- **Who answers**: each player, the higher seed (needs seeds from the
  tournament's own bracket), or the loser of the set's previous game.
- **Ask on**: every match, or only matches where a rule reading the question
  could fit, so a Game 3 question doesn't show up on game 1.
- **Opponent sees the answer**: right away, when the seed rolls, or never. Pick
  Never for an opt-in like Hard mode, so nobody can be dared into it.

Link a choice to a preset so a rule can roll **Players' answer**. Each question
in the list shows which rules read it, and you can't delete one a rule still
reads. Answers lock when the seed rolls.

Players get a DM with a link to their questions when a match is booked, and the
questions sit under their next match on Home.

## Sharing rules

**Actions → View as text** shows the saved rules as text you can copy, with
presets by name. **Import text** reads that text back into another tournament,
here or in another community with presets of the same names. **Copy from
tournament** takes another tournament's rules directly. Both replace the list
you're editing, and neither saves until you do.

## Why a match rolled what it did

Once a seed is rolled, the match on Admin → Match Schedule shows which rule fired
under its seed link, such as "Rule 2: Grand final mystery → Open (weighted
draw)". Only staff and proctors see it, so a mystery stays a mystery for the
players.
