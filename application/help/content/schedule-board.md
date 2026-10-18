---
title: Reading the schedule board
slug: schedule-board
icon: schedule
order: 20
summary: What each line on the Schedule board tells you, what each match state tells you, and how to filter it down to what you need.
---

The Schedule view on the Event tab lists every match in the event. Everyone sees
the same board: players, crew and spectators. Where a community uses brackets, the
tab's other view, Brackets, shows the tournament structure. For what's on stream,
see the Watch page ([Watching the event](/help/on-air)).

## The columns

:::snippet schedule-columns
Each match is one line, grouped under Live now, Up next, Waiting for players and,
if you've brought them back with the State filter, Finished.

| Column | What it tells you |
|---|---|
| Time | When it starts, in your local time. A date under it means it isn't today. |
| Match | Who's playing, and the tournament. "You" marks a match you're playing in. |
| Stage | Which stage it's on. If the stage is streamed, its name links to the stream. "Candidate" means staff are thinking about streaming it but haven't picked a stage. |
| Commentators, Trackers | Approved crew against how many the match needs, your own **Sign up** or **Withdraw** under that, then everyone who's signed up. |
| Status | Where the match is, plus **Acknowledge** on a match you're playing that you haven't confirmed yet. |
| Watch | The bell. Tap it to get a DM every time this match changes. |
:::

Tap a match's name, or anywhere on its line, for the details. Before the match
starts they open on a Ready to play? checklist: whether each player has confirmed,
their stations, the stage and its stream link, the seed once there is one, and how
much crew is still needed. Everyone signed up as crew is listed under that.

An empty cell or "None" just means there's nothing to show yet. A match usually
gets its stage, seed and crew at different times. "needs crew" in orange means a
match on a stage is still short of approved crew while signup is open.

## Match states

:::snippet match-states
A match goes through five states, in order.

| State | Meaning |
|---|---|
| {state:Scheduled} | A time is booked. Nobody has arrived yet. |
| {state:Checked In} | Both players are at their stations and a proctor has checked the match in. |
| {state:Started} | The match is under way. |
| {state:Finished} | Someone has recorded a result. It isn't official yet. |
| {state:Confirmed} | A staff member has confirmed the result. This is the official record. |

It's normal for a match to sit at {state:Finished} for a while before
{state:Confirmed}, because staff confirm results in batches. If a finished match's
result looks wrong, tell staff before it's confirmed.
:::

## Finding what you're after

The board hides finished matches by default. The State filter starts on
Scheduled, Checked In and Started, so a match drops off once its result is
recorded. Add Finished and Confirmed to the State filter to bring them back.

There are four filters above the board.

| Filter | Narrows the board to |
|---|---|
| Day | All dates, Today, Tomorrow, or the next 7 days. "Today" is your today. |
| Tournament | One or more tournaments. |
| Stage | One or more stages, handy for following one stream. |
| State | The match states you pick. |

The board remembers your choices for next time (just this board). If it looks
emptier than you expected, a filter from last time is probably still on.

On a phone the filters fold away behind a **Filters** button. The number beside it
counts how many aren't on their default, so a "2" means two filters are narrowing
what you see.

![The board's filters opened on a phone: Day set to All dates, Tournament and Stage empty, and State set to Scheduled, Checked In and Started](/static/help/schedule-board/filters-phone.webp "The filters on a phone, at their defaults.")

The **Needs crew** switch at the start of the filter row works on its own and isn't
counted with the filters. It shows only matches that haven't started yet and are
still short of an approved commentator or tracker. If you came to crew, that's the
list you want, and [Signing up as crew](/help/crew#signing-up) has the rest.

The board keeps its own order, so the columns don't sort. The refresh button
reloads it, but it also updates by itself when a match changes.

## Watching a match

:::snippet watch-match
**Watch** follows a match you're not in. You get a Discord DM each time its state
changes: checked in, started, finished, confirmed, rescheduled or cancelled.

If you're already a player or approved crew on a match, you hear about it anyway,
and watching it as well won't double your DMs.

Tap the bell in the Watch column, or at the top right of the match on a phone. It
fills in while you're watching. Tap it again to stop, or press **Unwatch** on any
DM it sends you.
:::

Watching a match is different from the community's Watch page. The page is a link
anyone can open to find the streams. See
[Watching the event](/help/on-air#the-watch-page).

## On a phone

On a phone, a tablet or a narrow laptop window, each line becomes a short card:
the time on the left, the players and tournament beside it, and the bell at the top
right. Your **Acknowledge** and each crew role's **Sign up** or **Withdraw** sit
underneath, so you don't need to open the match to use them. Tap the names for the
details.

![A match on a phone: its start time, Player One vs Player Two, Wizzrobe Cup on Stage 3, a Live chip, the bell, and Commentary 0/2 underneath](/static/help/schedule-board/card-phone.webp "One match, as a card on a phone.")

On a computer, the gear beside **Export CSV** lets you pick which columns you see
and in what order. That's saved to your account and doesn't change what anyone
else sees.
