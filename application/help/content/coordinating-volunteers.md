---
title: Building the volunteer schedule
slug: coordinating-volunteers
icon: event_available
order: 210
feature: VOLUNTEERS
roles: STAFF, VOLUNTEER_COORDINATOR
summary: Positions, shifts, the auto-scheduler, drafts and publishing, and covering a dropped shift.
---

This is the coordinator's side of [volunteering](/help/volunteering). You'll find
it at Admin → Volunteer Shifts, which staff and Volunteer Coordinators can open.
The roster, hour totals and the export are in
[Volunteer roster, hours and export](/help/volunteer-hours-and-export).

:::snippet vol-schedule
Volunteer Shifts shows one event day at a time. Pick the day at the top. The days
come from the event dates in Settings → Event. The **Actions** menu beside it has
auto-fill, positions, the export and the reset. A day runs from midnight to 6am
the next morning, so a shift that ends after midnight still shows on the day it
started.

The chips above the grid tell you how covered the day is. They show slots filled
against slots needed, how many are still open, how many assigned volunteers
haven't acknowledged, and how many drafts haven't been published.

Below that, each active position gets a card with its shifts for the day. A
shift's badge reads filled/needed and turns amber while it's short. On a phone,
a position with every shift filled folds away behind **Show N shift(s)**.
:::

![Volunteer Shifts for one event day: the coverage chips, the day picker and the Actions menu, marked 1, and the Check-in Desk position with its four shifts](/static/help/coordinating-volunteers/schedule-desktop.webp "The **Actions** menu (1) holds auto-fill, positions, the export and the reset.")

## Positions

A position is a job, like Registration or Stage Crew. **Actions → Manage
positions** lists them and opens each one for editing.

| Field | What it does |
|---|---|
| Name and description | What volunteers see on their shift card. |
| Display order | Where the position sits in the grid. Lower comes first. |
| Active | Untick to retire a position. It leaves the grid and the auto-scheduler, and its old shifts stay on record. |
| Shift length and stagger | Leave both blank for the four fixed blocks. Set both to make **Generate standard shifts** produce rolling shifts instead. |

**Generate standard shifts**, in the ⋮ menu on a position's card, fills the day
for you. With no length or stagger set, you get four blocks on the community's
clock: 08:00–12:00, 12:00–16:00, 16:00–20:00 and 20:00–midnight. With both set,
you get shifts of that length starting every stagger interval between 08:00 and
midnight, so people hand over one at a time. Each generated shift needs one
person. Use **Edit shift** to ask for more.

### Roles a position grants

A position can give an app role to whoever works it. That way the people on the
Proctor position get the Proctor role without anyone granting it by hand. Staff
set this up in Admin → Settings → Discord, in the Volunteer positions section at
the bottom of Role Mappings.

The role arrives when the assignment is published. It stays as long as the
person has any published assignment to that position, finished shifts included.
Drafts don't count. Unassigning them, them giving the shift back, or deleting the
shift or position takes the role away. A role someone was given by hand is never
touched.

Only operational roles can be mapped: Proctor, Broadcast Tech, Triforce
Submitter, Equipment Manager, Volunteer and Check-in Desk. Staff and the roles
that run a subsystem are left off on purpose, or whoever assigns shifts could
hand those out.

## Shifts

**Add shift** on a position's card opens the shift form. You set the position,
start and end, an optional label, how many people it needs and any notes. The
volunteer sees the notes on their shift card, and you see a note icon on the
shift that shows them on hover. **Edit shift** and **Delete shift** are in each
shift's ⋮ menu. Deleting asks first.

What people already on the shift hear depends on what you change:

| You change | What they hear |
|---|---|
| Label, notes or slots needed | Nothing. |
| Start or end time | A DM asking them to acknowledge again. Their old acknowledgment is cleared and the reminder is reset for the new time. |
| Delete the shift | A DM saying they're off it, for anyone whose shift hasn't finished. |

An edit never clears a check-in.

## Assigning someone yourself

**Assign** on a shift opens a list of the volunteer pool, which is everyone in
this community with the Volunteer or Staff role. Each name shows what they said
about that time:

| Badge | Means |
|---|---|
| {chip:ok:Preferred} | They'd like this slot. |
| {chip:ok:Available} | They can work it. |
| {chip:cancelled:Unavailable} | They ruled this time out. |
| {chip:neutral:Not stated} | None of their availability windows cover this shift. |

![The Assign picker for a Race Proctor shift with Show unqualified volunteers ticked and marked 1, each name carrying its availability badge, and Player Two's Qualified badge marked 2](/static/help/coordinating-volunteers/assign-picker-desktop.webp "**Show unqualified volunteers** (1) widens the list past the people marked **Qualified** (2).")

If anyone is qualified for the position, the list shows only them until you tick
**Show unqualified volunteers**. You set qualifications per person on the roster.

The app won't put someone on a shift they're already on, or on one that overlaps
a shift they already have. It warns you, but lets you go ahead, if the shift is
full or the person said they're unavailable or never said they're free.

A hand assignment is published straight away. The volunteer gets a Discord DM
with an **Acknowledge** button, and their chip on the shift shows where it
stands:

| Chip says | Means |
|---|---|
| Name · Draft | They haven't been told. |
| Name · Waiting | Published, not acknowledged yet. |
| Name · Acknowledged | They've acknowledged it. |

The **×** on a chip takes that person off, once you confirm. If the assignment
was published they get a DM about it. Removing a draft is silent.

Volunteers check themselves in. From 30 minutes before a shift they get an
**I'm here** button on every page, and a DM when the shift starts. A check beside
a name means they're checked in, and hovering it tells you when and whether they
did it themselves. A clock means they said they're running late. If someone
turns up without checking in, press the check-in button beside their name. Once
someone's checked in, they can't give the shift back from their phone, so they'll
need to come and talk to you.

If someone taps **Tell staff I'm late** in the shift-start DM, they keep the shift, and
every coordinator and staff member gets a DM saying how late they expect to be.

## The auto-scheduler

:::snippet vol-autofill
**Actions → Auto-fill from availability** drafts people into the open slots on
the day you're looking at. It asks three things first.

- Maximum hours per volunteer for the day. It starts at 8. Leave it blank for no
  limit.
- Whether to also use volunteers who haven't marked this time. It's off by
  default, so only people who said they're free get placed. Turn it on and
  people with nothing stated are used too, after everyone who did say something.
  Anyone who marked the time unavailable is never used.
- Which positions to fill. Running it again for one position replaces that
  position's drafts and leaves the rest alone.

For each open slot it picks someone qualified for the position first. After
that it prefers Preferred over Available over not stated, then whoever has the
fewest hours so far, then goes by name. It never double-books anyone or takes
them past the hour limit, and hand assignments already on the day count toward
both.

A volunteer with qualifications recorded only goes into those positions. One
with none recorded can go anywhere, behind the qualified people.

Nobody's told about any of this. Everything it does is a draft until you
publish.
:::

![The Auto-fill dialog: maximum hours set to 8, the option to use volunteers who have not marked this time, and all four positions selected](/static/help/coordinating-volunteers/autofill-dialog-desktop.webp)

After a run, a summary sits above the grid until you dismiss it or leave the
page. It lists:

- Open slots it couldn't fill, each with the main reason and an **Assign** button
  so you can fill it by hand. The reason is one of: nobody qualified said
  they're free, everyone eligible is at the hour limit, everyone marked it
  unavailable, or everyone's already on another shift. You'll see the same
  reason when you hover a short shift's badge.
- People placed outside their stated availability, if you allowed that, each
  with a **Remove** button.
- Heavy loads, meaning anyone within a quarter of the hour limit or working
  three or more blocks back to back.

## Publishing the draft

:::snippet vol-drafts
While a day has drafts, a banner says how many there are and that the volunteers
haven't been told. Check the grid and move people around by hand if you need to.
Then pick one of these.

- **Publish draft** asks you to confirm with **Publish & notify**, then DMs
  everyone on a draft assignment asking them to acknowledge. There's no quiet
  undo after that, because taking someone off sends them another DM.
- **Clear draft** asks first, then throws away every draft on the day. Nobody's
  told, since nobody was told about them in the first place.

If a draft clashes with a shift someone was given by hand in the meantime, it
stays a draft. The app lists those after publishing so you can sort them out.
:::

Publishing works one day at a time, so check the day picker before you press it.

## The Shift Board

:::snippet shift-board
Admin → Shift Board shows the same day as Volunteer Shifts, laid out the way
volunteers see their Full Schedule. There's a column per position, a row per
hour, and everyone's name in their shift. It's meant for the event itself, when
you need the whole day in front of you and want to fix a gap fast. Keep using
Volunteer Shifts to build the schedule and run auto-fill.

Each shift shows who's on it. Once it's started, a tick means the person checked
in, a clock means they said they're running late, and an empty circle means they
haven't turned up yet. A calendar tick means you called them off. Drafts show
in italics with a dashed underline, and a short shift says how many places are
open. A shift where everyone was called off is hatched.

Tap a shift to open its details. **Assign volunteer** adds someone there, and
each person's ⋮ menu lets you check them in, swap someone else in, move them to
another shift that day, or take them off. The ⋮ at the bottom edits or deletes
the shift. Tap an empty hour in a column to add a shift there, or use **Add
shift** at the top. **Columns** hides and reorders positions, just for you.
:::

## Calling off a shift

When you don't need someone after all, call them off instead of taking them off
the shift. Open the shift, then pick **Call off…** from their ⋮, or **Call off
whole shift…** from the ⋮ at the bottom to do everyone on it. You can add a
note, and they get a DM saying they don't need to come in. If the shift has
already started, the DM tells them they can head out.

A called-off shift still counts as fully worked, so it keeps their hours toward
comps and shows as attended in the reports. They skip check-in: no **I'm here**
banner, no reminder and no shift-start DM. Their My Schedule row says *Called
off*. If you change your mind before the shift ends, **Undo call-off…** puts
them back and DMs them that they're needed after all.

Swapping and moving tell people the same way doing it by hand does. Whoever
comes off gets a DM saying so, and whoever goes on is asked to acknowledge.
Drafts stay drafts and nobody's told.

## Reminders

Every published assignment gets one reminder DM before the shift. Staff set how
far ahead in Admin → Settings → Volunteers, and blank means an hour. If the
volunteer hasn't acknowledged yet, the reminder has the **Acknowledge** button
again. It also has a **Can't make it** button that opens their give-back dialog,
since the reminder is often when people realise they can't come.

Drafts never get reminders. Moving a shift's time resets the reminder for the
new time. Anyone who turned DMs off on their profile gets nothing, so check the
"awaiting acknowledgment" chip on the morning of each day.

## When someone gives a shift back

A volunteer can give back any shift that hasn't finished, as long as they
haven't been checked in for it. The slot opens straight away. Every coordinator
and staff member gets a DM saying who it was, how much notice they gave, their
reason if they left one, and a **Find cover** button that opens Volunteer Shifts
on that day.

## Starting over

**Actions → Reset all volunteer data** deletes every shift and every assignment
on every day. Positions, availability and qualifications stay. Anyone with a
shift still to come gets a DM saying they're off it. You have to type a
confirmation phrase and there's no undo, so export first if you might want the
old schedule back.
