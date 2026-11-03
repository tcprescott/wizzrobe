---
title: Setting up a tournament
slug: staff-tournaments
icon: emoji_events
order: 240
audience: staff
summary: Creating a tournament, its signup window and match hours, entrants, and who can run it.
---

Every match, signup, seed and payout hangs off a tournament, so get this right
before the event. Tournaments are on Admin → Tournaments.

## Creating one

:::snippet admin-tournaments
**Add tournament** opens the editor. Only the name is required. Everything else
starts collapsed with a sensible default, so you can save a name now and fill in
the rest later. When you reopen a tournament to edit it, every section starts
open.

Click a tournament's name in the table to edit it, or its player count to manage
who's entered. The ⋮ at the end of each row also has its bracket and rules links,
and deletes it once it's inactive.
:::

Leave **Active** ticked while the tournament is running. Untick it and the history
stays, but the tournament drops out of every picker, including the player's
Tournaments tab.

## What each section sets

| Section | What lives there |
|---|---|
| Scheduling | Average and maximum match length, players per match, the signup window, and per-day match hours |
| Entry & administration | Whether players may request matches, the rules link, format and randomizer notes shown to players, how many commentators and trackers a streamed match needs, and the stage reminder |
| Seeds & randomizer | The seed preset, an optional harder preset, and the triforce text access message |
| Integrations | The bracket link, Challonge, and racetime.gg room settings |
| Danger zone | Deleting the tournament. Staff only |

**Commentators needed** and **Trackers needed** decide when the coverage reports
count a streamed match as covered. Set one to 0 if this tournament doesn't use
that role. If you don't, every streamed match shows a gap and the schedule keeps
offering a signup nobody will take. **Broadcasters needed** works the same way
and only shows on an event with broadcasters turned on (Event settings → Crew).

**Remind about the stage** is how many minutes before the match a second DM tells
the players and approved crew which stage to go to. Assigning a stage already DMs
them once, right away. Set it to 0 to skip the reminder.

## The signup window

**Signups open** (1) and **Signups close** (2) set when players can enter
themselves on the Tournaments tab. Leave both blank and signups stay open until you
make the tournament inactive. The close has to come after the open.

The Signups column in the table shows where each tournament stands and when that
changes, for example "Not open yet — 2026-10-20 09:00 EDT" or
"Open — 2026-10-22 18:00 EDT".

The window only limits players. You can add or remove an entrant whenever you
like.

![The Add Tournament dialog with Scheduling open, showing the Player signups section with Signups open marked 1 and Signups close marked 2, then the Tournament Days section](/static/help/staff-tournaments/signup-window.webp "The signup window and match days, under Scheduling.")

## Match hours

Tournament Days overrides the community's event dates and daily hours for this one
tournament. Set a start and end date and you get a row for each day, with an open
and close time. Leave a day blank to allow any time.

Hours use the community's clock, and they apply to everyone, staff included. A
match can't be created, requested or moved to start outside them. If a tournament
needs a late finals slot, widen that day's close time first.

Leave the dates blank to use the community-wide hours under Settings → Event.

## Entrants

Click the player count to open the entrant list. Pick someone in
**Enrol a member** and press **Enrol** to add them. **Remove** beside a name takes
them out, after asking first. Staff and the tournament's admins can change
entrants. The person already
has to be a community member, so add them on Admin → Members first if they aren't.

Matches can only be scheduled between entrants. A missing entrant is usually why a
match won't save.

A tournament linked to Challonge gets its entrants from the bracket instead. See
[Running a tournament on Challonge](/help/staff-challonge).

## Who can run it

Two per-tournament jobs are set on the person, not on the tournament. On
Admin → Members, open the person's ⋮ menu, choose **Edit member**, and pick
tournaments under **Tournament Admin of** or **Crew Coordinator of**. A Discord role
mapping under Settings → Discord can grant either one too.

| Job | What they get for their tournaments |
|---|---|
| Tournament admin | The Schedule, editing the tournament, running and confirming its matches, rolling seeds, enrolling and removing its players, and its payouts |
| Crew coordinator | The Schedule, approving commentators and trackers |

Neither can create or delete a tournament or link it to Challonge. That stays with
staff. A tournament admin's Tournaments tab lists only the tournaments they run.

To find everyone with either job, use the **Roles** filter on Admin → Members. It
has **Tournament Admin** and **Crew Coordinator** options.

## Deleting a tournament

Deleting removes the tournament and everything attached to it: matches, entrants,
brackets and triforce texts. You can't undo it. Only an inactive tournament can be
deleted, so untick **Active**, save, then reopen the editor and use the Danger
zone. The confirmation lists what will go, and **Delete forever** stays greyed out
until you type `permanently delete`.

You almost always want to deactivate instead. That keeps the results.
