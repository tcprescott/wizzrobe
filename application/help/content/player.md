---
title: Being a player at the event
slug: player
icon: videogame_asset
order: 40
summary: My Schedule, acknowledging, getting a match booked, seeds and race rooms, asking for a different time, and your result.
---

This page covers how the app works for players. What happens on the floor
(check-in and stations, what you can have at your station, stage matches, what to
do when something breaks) is up to the event, not the app. If your community has
written that up, you'll find it under Event Guide in the sidebar.

## My Schedule

My Schedule is everything you've signed up for, on one page. Your matches come
first, then any crew slots, then the times you can't play, then any equipment
you've borrowed. (If you aren't in any tournament, your crew slots move to the
top.) The Event tab shows everybody's matches. This one shows only yours. Tap the
**?** at the end of a section's heading for help on that section without leaving
the page.

:::snippet player-schedule
My Schedule lists the matches you're playing in, in your local time. The Event
tab shows every match in the event. This one shows only yours.

Anything waiting on you sits above the list: an open race room, an opponent's
request to move your match, or a bracket matchup that still needs a time.
:::

![My Schedule on a phone: the List and Timeline switch, then the Your matches panel with an open race room and an opponent's reschedule request above the list](/static/help/player/my-schedule-phone.webp "Anything waiting on you comes first.")

The Your matches panel lists things in the order you need to deal with them:

1. *Your race room is open*, when an online match is about to start.
2. *Your opponent asked to reschedule*, when they've asked staff to move a match
   you're both in.
3. *Waiting on you to pick a time*, for bracket matchups with no time yet.
4. Your matches themselves, with a **Request Match** button if your tournaments
   take requests.
5. *Your change requests*, when you've asked staff to move something.

Your matches are laid out like the schedule board, one line each, grouped into
*Live now* and *Up next*. **Acknowledge** sits under the status, and the bell
watches the match. Everything else is in the match's **⋮** menu: **Ask to
change**, **Harder settings** if the tournament offers them, and **Offer for
stream**. Tap the match's name to see its details: your seed once it's rolled,
your station, and who's on crew.

A link from a Discord DM can narrow the list to one match. The chip at the top
says *Showing one match only*, and **Show all my matches** brings the rest back.

## Acknowledging a match

When you're put in a match, the bot DMs you an **Acknowledge** button. Tap it, or
tap **Acknowledge** on the match in My Schedule, to let staff know you've seen the
time and you'll be there. The match's details then count you under *Players
confirmed*.

You don't have to, and nobody's stopped from playing if they skip it. But your
proctor's *Ready to play?* checklist lists everyone who hasn't acknowledged, and
those are the people they go looking for. It's also not check-in. You still check
in at the event on the day.

If you booked the match yourself, it's acknowledged for you, and staff see it
marked *(auto)*.

## Getting a match scheduled

How a match gets on the board depends on the tournament. My Schedule only offers
the way your tournament uses.

### Request Match

Some tournaments let players book their own matches. Tap **Request Match**, pick
the tournament, your opponent, a date and a time, and tap **Submit**. The match
goes straight onto the board as {state:Scheduled}, and your opponent gets a DM.

:::snippet match-request
**Request Match** books a match: pick a tournament, your opponent and a time.

You and your opponent both have to be signed up for the tournament to pick it. If
the dropdown is empty, either you haven't signed up for anything, or your
tournaments run from a bracket. In that case you schedule your matchup from My
Schedule instead.

The time has to be in the future and inside the tournament's hours.
:::

### From a bracket

In a bracket tournament (on Wizzrobe or Challonge) you don't request matches.
Your next matchup shows up on My Schedule under *Waiting on you to pick a time*,
and the bot DMs you a button that opens the time picker.

:::snippet bracket-schedule
The card groups your matchups by tournament. Tap **Pick a time** next to the one
you want, choose a time you can both play, and tap **Schedule**. There's no
confirm step. Picking a time books it, and your opponent gets a message. If the
tournament takes change requests, they can ask staff to move it.

Either of you can book it, so talk to your opponent first. If they book it before
you do, the matchup drops off your list.

In a Challonge bracket, a matchup has no **Pick a time** button until your
opponent is matched to a Wizzrobe account, and you'll see the reason in its
place. The My Schedule tab shows a count while anything is waiting on you.
:::

![The Waiting on you to pick a time card, listing bracket matchups grouped by tournament, each with a Pick a time button or, for one that can't be booked yet, the reason why, with the first button marked 1](/static/help/player/bracket-schedule-phone.webp "Bracket matchups that still need a time: tap **Pick a time** (1) to book one.")

### Suggest a time

:::snippet suggest-time
**Suggest a time** fills in a slot for you. It picks the earliest time that
neither of you has blocked out, that doesn't clash with another match either of
you is in, that's inside the event's hours, and that the room has space for. If
either of you marked a time *Prefer*, that wins over an earlier one on the same
day.

It only fills in the fields. Nothing's booked until you submit, so check what it
picked and change it if it doesn't suit you.

If neither of you has blocked anything out, every hour of the event is fair game.
Block out what doesn't work for you under When you can't play.
:::

The whole set of rules, with an example, is in
[How Suggest a time works](/help/suggest-a-time).

## When you can't play

:::snippet player-availability
You count as available for every hour the event runs. When you can't play, on My
Schedule, is where you block out the hours you aren't: a shift, a flight, a night
off. Anything you leave alone stays open.

It's only used by **Suggest a time**. When you or your opponent are picking a
slot, that button skips anything either of you has blocked out and leans towards
anything either of you marked *Prefer*.

Blocking a time out is a hint. Staff can still schedule you into it, and your
opponent can still suggest it. Nobody's reading your times and slotting you into
the open hours, either.

The availability on the Volunteer tab works the other way round. There you say
when you *can* work, and you're only offered shifts inside the times you entered.
:::

## Your seed

For a randomizer tournament, the seed is the game you're about to play. Staff or
the proctor roll it shortly before the match. When it's ready, the bot DMs you a
*🎲 Seed ready* message with an **Open your seed** button. The same seed is under
Seed in the match's details on My Schedule (tap the match), with a **Copy**
button.

Some randomizers take a few minutes to make a seed. Meanwhile the details say
*Rolling…*, and you can leave the page. The DM still arrives.

Some randomizers don't give you a link. Wind Waker gives you a permalink and
Zelda 1 gives a seed and flags, which you paste into your own copy of the
randomizer. The DM shows it in a box you can long-press to copy, and the match's
details have a **Copy** button next to it. A Wind Waker permalink only works in
the build the DM names, so make sure you've got that one before you paste.

### Harder settings

:::snippet harder-settings
Some tournaments offer a harder preset alongside the standard one. **Harder
settings** in a match's **⋮** menu lets you opt in for one match, and the match
only plays harder if every player opts in.

Your answer is private. Nobody finds out you opted in unless everyone did, and
nobody's told if you say no. Once everyone's in, the menu item reads *Playing*
and the preset's name.

You can change your mind until the seed is rolled. After that, or if staff have
picked the match's settings themselves, there's nothing left to choose.
:::

## Your race room

If your match is online, it's played in a racetime.gg room. When the room opens,
the bot DMs you a *🏁 Your race room is open* message, and the top of My Schedule
shows *Your race room is open* with a **Join the room** button. The room opens in
a new tab.

Link your racetime.gg account under Connected accounts on your Profile so your
results are credited to you.

## Offering your match for stream

:::snippet stream-volunteer
**Offer for stream**, in a match's **⋮** menu on My Schedule, tells staff you'd be
happy for your match to go out on stream. Pick it once to offer, and **Withdraw
stream offer** in the same place takes it back.

Staff treat it as a hint. They build the stream schedule around stages, crew and
what the bracket needs. Offering doesn't book a stage, doesn't promise your match
gets streamed, and doesn't change how it's run.

If you both offer, staff see both names. Either of you can withdraw any time,
even after the match has started.
:::

## Running late

:::snippet running-late
From half an hour before your match until a proctor checks you in, your match
on My Schedule has a **Tell staff I'm late** button beside its status. Pick about how
late you'll be (10, 20 or 30+ minutes), add a note if it helps, and press **Tell
staff**. The admin on duty and the tournament's admins get a DM, and your match
shows up on their board as running late, so nobody calls it off while you're on
your way.

Your opponent isn't told. Your match's status then reads {chip:pending:Staff know: ~20 min},
with **Undo** beside it if you made it after all or tapped it by mistake. Send it
again to change the minutes.

A stage match's reminder DM has the same **Tell staff I'm late** button. If you can't
make it at all, use **Ask to change** instead.
:::

![The Tell staff you're running late sheet: the match and its time, a 10 min / 20 min / 30+ min choice, an optional note for staff, and the Tell staff button](/static/help/player/running-late-phone.webp "Pick about how late you'll be and press **Tell staff**.")

## Asking for a different time

You can't move or cancel your own match, but you can ask, and you don't have to
find a staff member to do it.

:::snippet reschedule-request
To move a match or call it off, open its **⋮** menu on My Schedule and pick **Ask
to change**. Nothing moves until staff say yes, so plan on the current time until
you hear back.

Give a reason, because that's what staff decide on. You can suggest a better
time, or leave it blank and let staff pick one.

Your opponent gets a DM with an **Agree** button. They don't have to agree, and
they can't block it. It just saves staff from asking you both.

Either way, you'll get a DM with the answer. If it's a yes, the match moves and
everyone in it is told. If it's a no, you'll see why, with an **Ask again**
button.

You won't see the option if the tournament doesn't take requests, if the match is
already checked in (find your proctor instead), or if its schedule comes from
SpeedGaming, which has to be changed there. Once you've asked, the **⋮** menu
shows *Change requested* until staff answer.
:::

![The Ask staff to change this match form: what you're asking for, a preferred date and time, and a required reason](/static/help/player/ask-to-change-phone.webp "The request form behind Ask to change.")

Your change requests, below your matches, shows what you asked for, whether your
opponent agreed, and what staff said. **Withdraw** takes back one that's still
waiting.

### When your opponent asks

If your opponent asks to move a match you're both in, *Your opponent asked to
reschedule* shows up at the top of My Schedule with their reason. **Agree** tells
staff the change works for you, but staff still decide. You don't have to answer,
and not answering doesn't hold anything up.

## If something changes

When a match is moved or cancelled, everyone in it gets a DM. You'll hear about
it, so there's no need to keep refreshing the board.

## Your result

The proctor records the winner when the match ends, and the board shows
{state:Finished}. That's not official yet. A staff member checks it and confirms
it, which moves the match to {state:Confirmed}.

If you and your opponent disagree about what happened, tell the proctor before
you leave. They'll record their best call and flag the match for staff, with a
note on what's in dispute. Staff settle it when they confirm the result.

## Needing an accommodation

If you need something arranged so you can play, like step-free access or extra
time between matches, ask from your Profile. See
[Asking for an accommodation](/help/accessibility).
