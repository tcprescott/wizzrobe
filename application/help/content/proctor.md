---
title: Running matches as a proctor
slug: proctor
icon: sports_esports
order: 55
feature: VOLUNTEERS
summary: The Proctor Station board, running a match start to finish, seating players, recording the result, the room's seeds screen and loaner devices.
---

A proctor runs a room. You check the matches in, start the players, and record
who won.

This page covers the app: the board, the steps you take in it, and what happens
to a result. How the job is done on the floor (looking after the room, who checks
players in, game integrity, when to fetch an admin, handing over a shift) is up
to the event. If your community publishes that, it's under Event Guide in the
sidebar. Read both before your first shift.

The board is Volunteer → Proctor Station. If you can't see it, you haven't been
given the Proctor role yet, so ask a coordinator. Being scheduled on a proctor
shift usually gives it to you.

Broadcast Techs get Proctor Station too, listing only the matches on a stage,
since those are the ones they run.

## The board

:::snippet proctor-station
Proctor Station shows every on-site match, grouped by what it needs from you
next: Late, check in now, then Ready to start, In play, Up next and Waiting for
players (matches whose players aren't decided yet). Each heading shows how many
matches are under it. Work from the top. Matches run from a racetime.gg room
are left off, because the room drives those.

Each line is one match. You'll see its time (orange when it's late), the players
with the station each one's sitting at, the stage, and a button for the next
step: **Check in**, **Roll seed**, **Start** or **Finish**. Press it and that
step's dialog opens, just like anywhere else in the app. Once a match is checked
in, its line also shows the seed, as the link and its code or as Seed not
rolled. A player who tapped **Running late** gets a tag on the line, like
{chip:pending:Player One ~20 min late · said 09:41}, until the match is checked in. The time and the stage are what you call out across the room.

Tap anywhere else on a line to open the match's details. They start with a
Ready to play? checklist covering players confirmed, stations, stage and seed.
Each item is ticked or flagged, with the fix on the same line: **Assign** or
**Change** for the stations, **Generate** or **Copy** for the seed, and the
player's ADA accommodation if they have one.

This board is for running matches, so it has no scheduling and no crew. That's
on the admin Match Schedule tab.

By default you see matches still to be played or in play. Use **Filters** to
narrow it to the day and the tournaments your room is running, or add Finished
to the State filter to see what you've already recorded, under Done. The search
box filters lines by any text in them, like a player's name.
:::

### Reading a line

![A line on the Proctor Station board on a phone: the time in orange, two players, No stations yet, an ADA note, and the Check in button marked 1](/static/help/proctor/board-phone.webp "A match waiting to be checked in: **Check in** (1) is the first step of running it.")

| You see | Means |
|---|---|
| Station 4 beside a name | The station they're seated at. On a phone the line sums it up instead, like Stations 3 and 7. |
| No station beside a name | They aren't seated yet. Check-in seats them. On a phone it reads No stations yet. |
| ADA: and a name | Staff have arranged an accommodation for them. Open the match and tap the accommodation to see it. |
| Live | The match is in play. |

The accommodation popup tells you something's been arranged and to check with
staff for what it involves. The details are for staff only, so find out before
the match starts.

## Running a match

:::snippet proctor-run-match
There are four steps, in order. Each one is the button on the match's line, and
it's also at the top of the match's details.

1. Check the match in once both players are there. Press **Check in**, pick a
   station for each player (or press **Randomize**), and press **Check in &
   seat**. The match goes to {state:Checked In} and the app records where each
   player is sitting. Stations are optional here. You can fill them in at Start.
2. Roll the seed if the match doesn't have one yet. The line's button says
   **Roll seed** until it does. You can only roll a seed once per match, and the
   players get it by DM. The code shows on the line so you can read it out.
   Tournaments without seeds skip this step.
3. Press **Start** when the players actually start, not when you seat them, and
   confirm. If the match isn't on a stage and a player still has no station,
   Start asks for stations first. Leave one blank to start anyway. To start
   without rolling a seed, open the match and press **Start match**. From then
   on everyone watching the board sees it as {state:Started}.
4. Press **Finish** when it ends and say who won. Tap the winner, then the
   **Yes** button with their name on it. Picking a name doesn't record anything
   by itself, so a mis-tap only costs you a **Back**. With three or four
   players, choose the winner from the list and press **Submit Results**. The
   match moves to {state:Finished}. A race with more than four players has no
   winner to record: Finish asks once, then closes the race out as confirmed.

What you do between those presses, like helping players set up, running a
countdown and keeping the room in order, is up to the event. The app records
what you did. It doesn't tell you how to run the room.

You don't confirm the result. A staff member does, and that's what makes it
official. Your job ends once you've recorded it. Until staff confirm, the match
shows {chip:pending:To confirm} under Done.
:::

![The Check in dialog: a station picker for each player, the first marked 1, the Randomize button marked 2, and Check in & seat marked 3](/static/help/proctor/check-in-phone.webp "Step 1: pick a station for each player (1) or press **Randomize** (2), then **Check in & seat** (3).")

### When the seed is slow or fails

Some randomizers take a few minutes to make a seed. The Seed line says Rolling…
with a clock while it works. You can leave the page, and the players get a DM
when it's ready.

If the last roll failed, the Seed line says why. Press **Roll seed** again.

"Couldn't DM" under a seed names any player who can't get Discord DMs. Read the
seed out or show it to them, because the DM won't reach them.

### Which stations to use

:::snippet proctor-stations
There's no rule for which stations a match goes on. That's your call. The app
only stops you putting two people in the same seat, or seating someone at a
station another match in play is using. A taken station shows in the picker as
in use, with the players sitting there.

**Randomize** picks a free station for each player and puts them on opposite
sides of the room, away from matches already in play where it can. Nothing's
saved until you press the button to confirm, so you can change either seat or
pick again. If the room's too full to manage all that, it tells you which part
it couldn't do and seats them anyway.

Players turn up one at a time, so you can seat them one at a time too. Tap the
station tag beside a player's name on the board (**+ No station**, or the
station they're at), type the number and press Enter. A clash shows under the
box, and nothing's saved until it clears.

Seated someone in the wrong place? Tap their station tag and type the new one.
Open the match and press **Change** on its Stations line to move everyone at
once.

You don't need to free a station afterwards. It frees itself as soon as its
match is recorded as finished.
:::

### If you didn't see who finished

:::snippet proctor-result
Ask both players and record what they agree on.

If they disagree, tick **Flag for admin review** first, then record your best
call. The box is under the player buttons, and pressing **Yes** saves the result
straight away, so tick it before you get there. Then tell an admin what
happened. The flag only marks the match. Talking to the admin is what settles
it.

Flagging doesn't hold the match up or change its state. It marks it
{chip:cancelled:Flagged} so nobody confirms it without looking.
:::

![The Enter match results dialog: one button per player with their station, the first marked 1, and the Flag for admin review checkbox marked 2](/static/help/proctor/result-phone.webp "Step 4: tap the winner (1). If the players disagree, tick **Flag for admin review** (2) first.")

### If you recorded the wrong winner

You can't change a result once it's recorded. An admin has to fix it. Tell one
which match (its `#`) and who actually won.

## The room's seeds screen

A room can have a shared screen showing the seed for every match about to be
played, so players can read it without anyone signing in. It's a web page opened
from a private link that staff give out for that machine.

It lists matches that have a seed and haven't started. A match drops off as soon
as you press **Start**, and new seeds show up as they're rolled. Leave it open,
it updates itself.

The screen can't roll seeds or change anything. If a match is missing, it hasn't
got a seed yet, so roll one from the board. If the screen shows a page-not-found
message, its link has been withdrawn or mistyped. Ask staff for a new one instead
of signing in on the machine.

## Lending a device

If the event lends out equipment, each loaner has a QR code on it. The player
who's going to use it scans the code with their phone, signs in, and borrows it
in their own name.

Point them at the code instead of doing it for them. A proctor can't check a
device out for someone else, and borrowing it on your own account puts the loan
in your name.

Returns go to whoever runs the equipment desk. Only they can check a device back
in, so if a player is packing up with a borrowed unit, send them and the unit to
the desk.

[Borrowing equipment](/help/equipment) covers the whole loan, from the player's
side and the desk's.
