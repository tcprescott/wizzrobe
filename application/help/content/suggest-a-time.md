---
title: How Suggest a time works
slug: suggest-a-time
icon: lightbulb
order: 42
summary: How Suggest a time picks a slot, why it fills the room early instead of spreading matches out, and what it doesn't check.
---

**Suggest a time** is the button in the booking and match dialogs that fills in a
date and time for you. This page explains exactly how it picks one, so you can
tell why it chose 10:30 and not 9:30.

It only fills in the form. Nothing's booked or held until you save, and you can
change the time before you do. Saving checks the time again, see
[Checking the time when you save](/help/suggest-a-time#checking-the-time-when-you-save).

![A player's booking dialog on a phone, with the date and time filled in and the Suggest a time button marked 1](/static/help/suggest-a-time/suggest-phone.webp "Tap **Suggest a time** (1) and it fills in the date and time.")

## What it looks at

- **How long a match runs.** The tournament's average match length, or 90
  minutes if the tournament hasn't set one.
- **When matches can run.** The event's dates and each day's hours, or the
  tournament's own dates and hours if it has them. Times are on the community's
  clock.
- **Everyone's other matches.** Every match with a time that isn't finished and
  confirmed yet, in any tournament in the community.
- **How many players the room holds.** Staff set this as *Players at once*.
- **Each player's availability.** The times you've blocked out, and the times
  you've marked *Prefer*, under When you can't play on My Schedule.
- **The bracket round.** In a bracket with round times set, the match has to fit
  inside its round, start to finish.

When you're moving a match that already has a time, its current slot doesn't
count against it.

## How it picks

It starts by listing every start time on the half hour (9:00, 9:30, 10:00…)
inside each day's hours. A start time only makes the list if the whole match
finishes by closing time, so with a midnight close and 90-minute matches, the
last one is 22:00.

Then it crosses out any start time where:

- one of the players has blocked out any part of the match,
- one of the players is already in another match that overlaps it, or
- adding this match would put more players in the room than it holds, at any
  point while the match runs.

From what's left, it picks in this order:

1. **A slot that keeps the room three quarters full or less** beats any slot
   that doesn't, even one on an earlier day. With a 40-player room, that's 30
   players.
2. **An earlier day** beats a later one.
3. On the same day, the slot more of you marked *Prefer* wins.
4. Then **the earliest time** wins.

Only when no slot keeps the room at three quarters does it go fuller, up to the
limit and never past it.

### It looks at the next four hours first

It runs that whole process twice. First it only considers start times in the
next four hours. If anything at all fits there, that's your answer, even if it
puts the room above three quarters. Only when nothing in those four hours fits
does it look at the rest of the event.

So on the day, it'll usually offer you something soon rather than tomorrow
morning.

## Why it fills early

It packs matches into the earliest time that still leaves a quarter of the room
free. It doesn't spread them out across the event.

Spreading them out sounds fairer, but it pushes group matches to the last
night, and then later rounds have nowhere left to go. Filling early gets each
round done sooner, and the spare quarter is there for walk-ups and matches that
run long.

## An example

The room holds 40. Alice and Bob need a 90-minute match, and Alice presses
**Suggest a time** on Thursday at 9:10. Already booked:

- 9:00 to 10:30: 20 players
- 10:00 to 11:30: 10 more players
- 12:00 to 13:30: Bob, in another tournament

| Start | Players in the room at its busiest | What happens |
|---|---|---|
| 9:30 | 32 | Fits, but over three quarters |
| 10:00 | 32 | Fits, but over three quarters |
| 10:30 | 12 | **Picked** |
| 11:00 to 13:00 | | Crossed out, Bob's other match |

It picks 10:30. 9:30 had room, but it would have put 32 people on the floor.

If Alice had blocked out 9:00 to 14:00, nothing in the next four hours would
fit, so it would look further ahead and pick 14:00.

## What it doesn't check

- **Crew and volunteer shifts.** If you're commentating, tracking or working a
  shift at a time, Suggest a time can still pick it. Block it out under When you
  can't play if you want it skipped. The check when you save does catch these.
- **Requests waiting for staff.** A request to move a match doesn't hold its new
  time until staff approve it.
- **Matches running long.** Other matches are counted at their tournament's
  average length.
- **Someone else booking at the same moment.** Two people pressing the button
  at once can both get the same slot. Nothing's held until you save, and the
  check when you save is what catches it.

It also reads days without hours differently from saving. When staff set hours
for some days and leave others blank, Suggest a time treats the blank days as
closed and never offers them, but a time typed in by hand on one of those days
still saves (after the check below warns you).

## Checking the time when you save

Whatever time you end up with, suggested or typed, it's checked again when you
press **Schedule**, **Submit**, **Create** or **Save**. If something might be
wrong, a *Check this time* sheet lists why:

- a player is already in another match then,
- a player is commentating or tracking a match then,
- a player isn't free then (a volunteer shift or a time they blocked out),
- the room would hold more players than it's allowed,
- the match would run past closing,
- no hours are set for that day, or the day is outside the event.

Press **Change time** to go back, or **Schedule anyway** to book it. It never
stops you. A start time outside the day's hours is different: that one can't be
saved at all.

![The Check this time sheet on a phone: the time asked for, the reason it might not work (the other player isn't free then), with Change time marked 1 and Schedule anyway marked 2](/static/help/suggest-a-time/check-this-time-phone.webp "**Change time** (1) goes back to the form. **Schedule anyway** (2) books it.")

You see the details of your own shifts and blocked-out times. For another
player you only see that they aren't free, not why. Staff and the tournament's
admins see everything.

Changing something other than the time when you edit a match doesn't bring the
check up again.

## When it can't find anything

You'll see *No available slot found for these players within the event
schedule*. Usually that means one of you has blocked out most of the event, or
the room is full for every slot that's left. In a bracket, *…inside this round's
scheduled window* means the round's time is used up. Pick a time yourself, or
ask staff.
