---
title: Running the schedule
slug: staff-schedule
icon: edit_calendar
order: 200
audience: staff
summary: For staff. Admin → Match Schedule, creating and editing matches, cancelling one, rewinding a match, stages and stream candidates, and approving crew.
---

This one's for staff. It covers Admin → Match Schedule, the board you book and run
matches from. What players, crew and proctors see of the same matches is in
[the player guide](/help/player), [crew signup](/help/crew) and
[the proctor guide](/help/proctor).

Reschedule requests, results to confirm, disputes, seeds and race rooms (the
things waiting on you) have their own page,
[Match day queues](/help/staff-match-day).

## The board

:::snippet staff-schedule-board
Admin → Match Schedule lists every match in the community, one line each: the time,
who's playing, the stage, the crew and where the match is at. By default it hides
{state:Confirmed} matches, since those need nothing more from you. Change that with
the **State** filter, and narrow by day, tournament, stage or a search, the same way
as [the public board](/help/schedule-board).

Lines are grouped by what they need. Needs a decision (finished, not yet
confirmed), Live now, Up next, Waiting for players (bracket matches whose players
aren't decided yet) and Confirmed. A time in orange with a {chip:pending:Late} chip
means the match is past its start and nobody's checked it in.

The Commentators and Trackers columns count signups against what the tournament
needs, so "1/2" is one signup for two places. Until the match starts, a role
that's short says "needs crew" in orange, and one with enough signups you haven't
approved yet says "to approve". A match with no stage needs no crew, so it shows
just the signup count. The names are listed under the count, with a tick for
approved and a clock and "to approve" for the rest.

Tap a line to open the match. Its next step (**Check in**, **Start match**,
**Finish** or **Confirm result**) sits under the title. Below that, the Ready to
play? checklist covers the match before it starts: players confirmed, stations,
stage, seed and crew. Each one's ticked or flagged, with the fix on the same line
(**Assign** stations, **Generate** a seed, pick a stage). No stage is fine. It just
means the match isn't streamed. **Edit match** at the bottom opens the full dialog,
and **New match** at the top of the page adds one.

Needs you, beside the board on a computer and above it on a phone, lists the work
that's waiting on you instead of on the clock. See
[Match day queues](/help/staff-match-day).
:::

![The Admin Schedule board: one line per match, grouped into Needs a decision, Live now and Up next, with a Flagged chip marked 1, a commentary count reading needs crew marked 2, and a tracker signup reading to approve marked 3](/static/help/staff-schedule/board-desktop.webp "A result to decide (1), a role that **needs crew** (2), and a signup **to approve** (3).")

![A match opened from the board: its next step, Check in, marked 1, then the Ready to play? checklist of players, stations, stage, seed and crew, each ticked or flagged, with the stations' Assign fix marked 2 and Edit match at the bottom marked 3](/static/help/staff-schedule/match-sheet.webp "Tap a line to open the match: its next step (1), a fix on the checklist (2), and **Edit match** (3).")

### Matchups nobody has booked

Challonge, start.gg and Wizzrobe brackets turn up matchups that are ready to play,
and only their two players see them, on My Schedule. Switch the board from
**Booked** to **Not booked yet** to see every one that's still waiting, grouped by
tournament. The switch counts them.

**Book…** schedules a matchup for both players, the same as a player booking it,
with Suggest a time and the clash check. Outside the round's booking window you're
warned and can book anyway. A matchup marked {chip:pending:Needs account} has a
player who isn't matched to a Wizzrobe account yet, so nobody can book it. The line
names them, and **Fix entrant** opens the place to match them. **All**,
**Ready to book** and **Needs account** narrow the list, and the search finds a
player.

### Who sees what

Other people use this board too. Each role only gets the controls it can use.

| Role | What they can do here |
|---|---|
| Staff | Everything below |
| Tournament admin | Everything, but only for their own tournaments. The board shows them nothing else. |
| Broadcast tech | Set the Stage on any match, and check in, roll the seed, start and finish any match that's on a stage |
| Crew coordinator | Approve commentators and trackers on their tournaments |
| Proctor | Check in, start, finish and roll seeds, from Volunteer → Proctor Station rather than here |

Only staff and tournament admins confirm results. A proctor records the winner, and
you make it official.

## Creating a match

Press **New match**, pick the tournament, the players, a date and a time, then press
**Create**. Everything else is optional.

- Players only lists people entered in that tournament. Tick
  **Include players not enrolled in this tournament** to pick anyone in the
  community. That enrols them, and the dialog tells you who it's about to enrol
  before you save.
- **Suggest a time** fills in the date and time. It picks the earliest slot that
  none of the players has blocked out or is already playing in, and it never pushes
  the room past Players at once (Admin → Settings → Event). It fills the room to
  about three quarters first, so walk-ups still fit.
- Commentators and trackers you add here are approved on the spot. They get a DM
  asking them to acknowledge.
- **Stage** puts it on a stream right away. **Stream candidate** marks it as
  "probably streamed, stage not decided".

Saving DMs the players and asks each of them to acknowledge.

## Editing a match

Open the match and press **Edit match**. The top of the dialog shows where the match
is now (its state, the winner, and whether it's flagged), and everything below that
edits it.

![The Edit Match dialog for a finished match, showing its Finished state, its winner and a Needs review flag, marked 1, above the fields](/static/help/staff-schedule/match-dialog.webp "A flagged result (1) shows at the top of the dialog.")

Moving the date or time tells everyone on the match and asks the players to
acknowledge again. Changing the players asks the new line-up to acknowledge, and a
player you take off gets a DM saying so. Commentators and trackers you add or remove
get a DM too.

A match synced from SpeedGaming has a Synced from SpeedGaming badge. Its tournament,
players and time are locked, because the next sync would overwrite your change.
Edit those on SpeedGaming.

### Seed settings

When a tournament offers a second, harder preset, the dialog shows Seed settings
until the seed is rolled. Leave it on **Let the players decide** and the harder
preset is only used when every player opts in. Pick **Force** either way to make
the call yourself. Both players get a DM whichever you pick.

### Rewinding a match

Open the dialog's ⋮ menu and choose **Rewind…** when a step shouldn't have
happened, like a match checked in by mistake or a result confirmed too soon. Pick
the step to go back to (**Finished**, **Started**, **Checked in** or
**Scheduled**) and every step after it is cleared at once. The menu only offers
steps the match has actually reached.

To re-roll a seed, choose **Clear seed…** from the same menu and confirm. Once it's
gone, the match's Seed line offers **Generate** again.

If only the winner's wrong, don't rewind. Open the match and press
**Change result** instead (see [Match day queues](/help/staff-match-day)).

## Calling a match off

The match dialog's ⋮ menu, left of Cancel and Save, has two ways to get rid of a
match, and they do different things.

| Menu item | What it does | Use it when |
|---|---|---|
| **Call off match…** | Removes the match, DMs its players and crew, and closes any open race room | The match was real and people were planning around it |
| **Delete match…** | Removes the match and tells nobody | It should never have existed: a duplicate, a test, a typo |

If a player asked to call it off, approve their request instead (see
[Match day queues](/help/staff-match-day)). That cancels it the same way and answers
them in one go.

## Stages and streams

:::snippet staff-stage
Open the match and pick from **Stage**. The board's Stage column shows what each
match is on.

- A stage name puts the match on that stream. **Watch** beside it opens the stream.
- Candidate means "this is going out, we haven't picked a stage yet". Set it while
  you're still planning, and the coverage report counts it.
- No stage takes it off.

Switching from Candidate to a real stage keeps the match counted as a stream match
in the reports, so there's nothing to untick.

When a player offers their match for stream, the dialog names them under Stream
candidate. That's only a hint. The match doesn't become a candidate until you tick
the box or pick Candidate on it.
:::

The stages themselves (name, stream URL, and whether they're active) are on
Admin → Stages. Staff and broadcast techs can add and edit them there. Untick
**Active** on a stage you've stopped using.

## Approving crew

:::snippet staff-crew-approval
Commentators and trackers who sign themselves up start out unapproved. Open the
match. Under Crew, each role lists the people Waiting for you, each with an
**Approve** button, and says how many more it needs. Approved people are listed
above, after "Approved:", as a name with "confirmed" or "hasn't confirmed". Their
⋮ menu has **Un-approve**.

Approving asks you to confirm, and warns you if that person's already booked
somewhere else at the same time. Once you approve, they get a DM asking them to
acknowledge. Un-approving clears their acknowledgment and tells them.

Press a waiting name, or **Open profile** in an approved person's ⋮ menu, to see
their profile.
:::

### Finding gaps in coverage

Admin → Reports → Staff / Crew Activity is where you find matches that are short of
crew. Coverage by match counts approved against total signups for each role, next
to what the tournament needs, and marks every short match with GAP. Filter it to
**Pending only** to see signups still waiting on you. Each row opens that match on
the board, where you can approve someone.

Contribution by person shows how much each volunteer has covered, which helps when
you're picking between two people for the same slot.
