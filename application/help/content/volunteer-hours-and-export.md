---
title: Volunteer roster, hours and export
slug: volunteer-hours-and-export
icon: groups
order: 220
feature: VOLUNTEERS
roles: STAFF, VOLUNTEER_COORDINATOR
summary: Who is in the volunteer pool, qualifications, hours against reward tiers, the settings behind them, and the CSV export.
---

Admin → Volunteers lists everyone you can schedule. Staff and Volunteer
Coordinators can open it. Building shifts and assigning people is covered in
[Building the volunteer schedule](/help/coordinating-volunteers).

## The roster

:::snippet vol-roster
The roster is everyone in this community with the Volunteer or Staff role. That
role is what puts someone in the pool, so if a volunteer's missing, check their
roles on the Members tab or the Discord role that grants it.

| Column | Shows |
|---|---|
| Hours | Their scheduled hours for the event, with the highest reward tier they've reached. |
| Qualifications | The positions they're qualified for. A dash means none recorded. |
| Availability | Every window they entered, marked Preferred, Available or Unavailable. |

The manage button at the end of a row opens their full availability and a
checkbox for each active position. Tick the positions they're qualified for and
press **Save qualifications**.
:::

![Volunteers: each volunteer's hours with the reward tier reached, qualifications and availability windows, with the manage button on Player One's row marked 1](/static/help/volunteer-hours-and-export/roster-desktop.webp "The manage button (1) on a row opens that volunteer's availability and qualifications.")

Qualifications change where the auto-scheduler puts people. Someone with at
least one qualification only goes into those positions, ahead of unqualified
people. Someone with none can go anywhere, behind the qualified people. The
Assign picker also hides unqualified people by default for any position that
has qualified ones.

Volunteers add and edit their own availability on the Volunteer tab. You can
read it here but you can't change it.

## Hours and reward tiers

:::snippet vol-hours
Hours are the time a volunteer is scheduled for during the event, counted from
published assignments. Drafts don't count. Overlapping shifts count once, so
08:00–12:00 and 10:00–14:00 make six hours, not eight. Only time between the
event's start and end dates counts, so last year's shifts don't earn this year's
reward.

These are scheduled hours, not checked-in hours. If a volunteer gives a shift
back, they lose those hours straight away.

The chip beside the number is the highest reward tier they've reached. Each
volunteer sees the same total on their own My Schedule, along with how far they
are from the next tier.
:::

Staff set the tiers in Admin → Settings → Volunteers as hour totals separated by
commas. Blank means 8, 12 and 16. Enter 0 if your community doesn't reward
hours.

If the event uses check-in with volunteer comps turned on, the lowest tier is
also the bar for a comped badge. Anyone whose scheduled hours reach it shows on
the check-in roster as a volunteer comp.

## Settings only staff can change

Coordinators will want these changed now and then, but only staff can reach
them:

| Setting | Where |
|---|---|
| How long before a shift the reminder DM goes out, and the reward tiers | Admin → Settings → Volunteers |
| Which app role each position grants | Admin → Settings → Discord, the Volunteer positions section of Role Mappings |

## Exporting the data

**Export data** is on Volunteers, and in the **Actions** menu on Volunteer
Shifts. Pick a date range (it starts as the event dates) and press **Export**.
The ZIP builds in the background and downloads when it's ready; it's also kept
for 3 days under Admin → Reports → Exports, in case you close the dialog
first. It holds a README and six CSV files:

| File | One row per |
|---|---|
| volunteers.csv | Everyone in the pool or on a shift, with their qualifications, available hours and assigned hours. Always the full roster, whatever the dates. |
| availability.csv | Availability window someone entered, including the times they ruled out. |
| positions.csv | Position, with its shift and slot counts. |
| shifts.csv | Shift in the date range. |
| assignments.csv | Person on a shift, whether it's a draft, whether it fits what they said they were free for, and when they acknowledged and checked in. |
| schedule.csv | Slot, open ones included. This is the sheet to draft a schedule on. |

Times are in your own time zone, and every file also has UTC timestamps for
sorting. The README says which zone it used.

The export is only a copy. Editing the files doesn't change anything in the app,
so a schedule you build in a spreadsheet has to be entered on Volunteer Shifts
by hand. It includes everyone's availability and notes, so keep it private. Each
export is recorded in the audit log.

## Coverage report

To look across several days, Admin → Reports → Volunteer Coverage lists every
understaffed shift in a date range and how many more people each one needs. A
volunteer coordinator who isn't staff gets a Reports tab with just this report.
The match, crew and stage reports stay with staff, tournament admins and crew
coordinators.
