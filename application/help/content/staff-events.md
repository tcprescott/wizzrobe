---
title: Running more than one event
slug: staff-events
icon: calendar_month
order: 238
roles: STAFF
summary: Starting next year's event from this one, going live, the default event, archiving last year's, and the event argument in the API.
---

A community keeps what lasts from year to year: members, staff, integrations,
presets, equipment, webhooks and notification settings. An **event** holds one
run of it: its tournaments and matches, brackets, stages and stations, volunteer
positions and shifts, check-in, meetups and the event guide. Admin →
Configuration → **Events** lists them all.

Any number of events can be live at once, say the on-site weekend and an online
season. One of them is the **Default**: a link with no event in it opens that
one. Everyone else moves between events with the event button in the header,
which shows up once there's more than one to pick from.

## Starting a new event

Press **New event**, give it a name and, if you know them, its dates. **Copy
setup from** starts on the default event, with every group below it ticked:

| Group | Brings |
|---|---|
| Tournaments | Names, formats, presets, Discord role mappings. No players, brackets, round schedules or Challonge/start.gg links. |
| Volunteer positions | With who is qualified for each, and the hour reward tiers. |
| Stages and stations | With how stations are numbered. |
| Check-in tiers | Badge tiers and comp rules. No attendees. |
| Discord roles from Wizzrobe | The rules, not the roles already given. |

Untick what you don't want, or pick **Nothing, start empty**. To copy from a
particular event, use **New event from this…** in that event's ⋮ menu instead.
A rule that can't be copied is named in the message after **Create draft**.

The new event is a **Draft**. Only staff can see it, plus anyone you give a role
in it (a proctor or volunteer coordinator for next year, say), so you can set it
up without players stumbling on it. Its link name comes from its name;
**Rename…** changes either while it's still a draft.

## Going live and the default event

:::snippet staff-events-lifecycle
**Go live…** opens the event to everyone in the community. It doesn't move the
default: the old event keeps answering bare links until you pick **Make
default** on the new one. Do that when most people should land on the new event,
usually once its schedule is up.

Once an event is live its link name is fixed, because DMs and bookmarks already
point at it. You can still rename it.
:::

## Event settings

**Event settings** in a row's ⋮ menu (or Run the event → Event settings while
you're in that event) holds what changes from one event to the next: the dates
and hours, capacity, venue, its timezone if it differs from the community's,
the crew Broadcasters switch, volunteer reward tiers and Discord roles.

## Roles in one event

Staff and the online admin roles cover every event. The on-site roles (Proctor,
Broadcast Tech, Triforce Submitter, Volunteer Coordinator, Equipment Manager,
Volunteer and Check-in Desk) are held in one event, so last year's proctors
aren't this year's until you say so. **Edit member** on Admin → Members shows the
community-wide roles first, then the roles in the event on screen, with a line
for any they hold in other events.

## Archiving last year's event

:::snippet staff-events-archive
**Archive…** makes an event read-only, for staff too. Its pages stay up for
everyone, with a banner saying it's archived, so last year's brackets and
results are still there to look at. The dialog lists anything still open
(unfinished matches, shifts still to come, open race rooms) so you can tidy up
first, and asks which event becomes the default when you're archiving that one.
With no other live event it stays the default, read-only, until another goes
live.

When it's archived:

- its background jobs stop: no reminders, syncs or race rooms for it;
- open race rooms are cancelled and seed rolls still waiting are dropped;
- Discord roles its rules gave are taken back;
- 30 days later its emergency contacts and accommodation requests are deleted.

**Unarchive…** (in the ⋮ menu, or on the banner) puts it back to live and starts
its jobs again. Discord roles come back; deleted emergency contacts and
accommodation requests don't.
:::

## Deleting a draft

**Delete…** is only there for a draft with no matches. It deletes the event and
the setup copied into it. A live or archived event can't be deleted, only
archived.

## Events in the API and the connector

The REST API and the Wizzrobe MCP connector work in the default event unless
you name another. Pass `event` with the event's link name: `?event=sgl27` on a
REST call, or an `event` argument on a tool. A call about one match or shift
works in that row's own event. `GET /api/events` and the `list_events` tool list
the events you can see, and `whoami` lists them per community. The audit log,
sent messages and engagement telemetry are community-wide, so there `event` only
narrows the results.
