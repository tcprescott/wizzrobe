---
title: Community events
slug: community-events
icon: groups
order: 67
feature: COMMUNITY_EVENTS
summary: Finding meetups and side events attendees are running, getting a reminder or RSVPing, and listing your own.
---

Community events are the meetups, panels, side tournaments and board game nights
attendees run for each other. They aren't part of the official programme, and the
page says so at the top. Staff don't endorse what's listed. They just give it a
home.

You have to be a member of the community to see the list or add to it.

## Finding events

Open Event on Home and switch to **Community** at the top. You'll see every event
that hasn't finished yet, grouped by day, soonest first. Each card shows the time
and length, where it is, and who's hosting. An event that's on right now has a
{chip:live:Happening now} chip.

When new events have gone up since you last looked, the Event tab and the
**Community** switch both show how many. Those events carry a {chip:new:New} chip
while you look through them, and the count clears once you've opened the list.
Your own events never count as new.

Tap an event's title for its own page and the full description.

## Getting a reminder or RSVPing

:::snippet community-events-attend
Each event has one button, and which one depends on the host.

| You see | What it does |
|---|---|
| **Remind me** | DMs you on Discord shortly before it starts. The button changes to **Subscribed**. Tap that to stop the reminder. |
| **RSVP** | Tells the host you're coming and gets you the same reminder. The button changes to **Going**. Tap that to cancel your RSVP and the reminder with it. |
| **Full** | The host set a cap and every place is taken. If someone cancels, the button comes back. |

If you tapped **Remind me** before the host turned RSVPs on, you keep your
reminder. The card shows **Subscribed** beside **RSVP** so you can still turn it
off.

The card shows how many people are going (and the cap, if there is one) or how
many have subscribed.

You'll also get a DM if the event moves or gets called off. These are all DMs, so
they only reach you while Discord DMs are on in your
[notification settings](/help/notifications#where-your-settings-live).
:::

![The Community list on a phone: the not-official notice and Submit an event, then events grouped by day, each marked New on a first visit. each card showing its start time and length. Morning coffee run is Happening now with a Remind me button marked 1, Board game night shows how many are going with an RSVP button marked 2, and Speedrun trivia is full, with a greyed-out Full button marked 3. Your events at the bottom lists a cancelled event](/static/help/community-events/listing-phone.webp "**Remind me** (1), **RSVP** (2) and **Full** (3), depending on the event.")

The reminder goes out 30 minutes before the start, unless staff have picked a
different time for the community.

## Submitting your own

:::snippet community-events-submit
Tap **Submit an event** at the top of the list. Give it a **Title**, when it
**Starts**, and how long it runs (anything from 5 minutes to 72 hours). **Where**
and **Description** are optional, but people are more likely to turn up when they
know where to go and what to bring. The description can have headings, lists and
links.

Turn on **Take RSVPs** if you want to know who's coming. You can set a
**Cap (optional)**, or leave it blank for no limit. Once the cap's reached, the
button says **Full** for everyone else.

What happens after **Submit** depends on the community.

- If staff review events (the default), yours waits until they've looked at it.
  You'll get a DM when they approve it, or when they turn it down, with the reason.
- If they don't, it's listed straight away.

The form tells you which one applies before you submit.
:::

![The Submit a community event form on a phone, scrolled to Length, Where and Description, with the Take RSVPs switch turned on and its Cap (optional) box marked 1, and the Submit button marked 2](/static/help/community-events/submit-phone.webp "Switch on **Take RSVPs** to set a cap (1), then **Submit** (2).")

Your submissions show up under Your events at the bottom of the page, each with
its status.

| Status | Means |
|---|---|
| {chip:pending:Waiting for review} | Staff haven't looked at it yet. Only you and staff can see it. |
| {chip:ok:Listed} | It's on the list and people can subscribe or RSVP. |
| {chip:cancelled:Not listed} | Staff turned it down or took it off the list. Their reason shows underneath as "Staff said: …". |
| {chip:neutral:Cancelled} | You or staff called it off. |

## Seeing who's going

Your own event's page has a Who's going list with everyone who's RSVP'd, in the
order they did it. Only you and staff see the names. Everyone else sees the count.
The list only shows up when the event takes RSVPs.

## Changing or cancelling your event

Open the event's page and tap **Edit** or **Cancel event**.

Edits to a listed event go live straight away, with no second review. If you move
the start time, everyone subscribed or going gets a DM with the new time, and their
reminder moves with it. You can lower the cap, but not below the number of people
already going. If you try, the app tells you how many that is.

If staff turned your event down, fix whatever their reason asked for and save it.
That sends it back to {chip:pending:Waiting for review} and lets staff know.

**Cancel event** asks first. The event comes off the list, and everyone who
subscribed or RSVP'd gets a DM saying it's off. You can't edit or bring back a
cancelled event, so submit a new one if plans change again.

## Reviewing events (staff)

This part's for staff. Everyone else can stop here.

:::snippet community-events-review
Submissions are in Admin → Community Events. You get a DM when a member submits
one, and its button opens that event's review dialog. The **Show** filter starts on
*Waiting for review* whenever something's waiting.

**Review** on a row opens the event and its description. From there you can use
these.

| Button | Does |
|---|---|
| **Approve** | Lists it, and the host gets a DM. It also puts back an event you took down, but its old subscribers and RSVPs don't come back. |
| **Reject** | Turns it down. Write a reason first. The host gets it in a DM and can edit and resubmit. |
| **Take down** | Same as Reject, for an event that's already listed. Anyone subscribed or going is told it's cancelled, and their subscriptions and RSVPs are cleared. |
| **Edit event** (in the ⋮ menu) | Changes the event yourself. It keeps its status. |

Events staff submit are listed straight away, and staff can edit or cancel any
event from its own page, just like its host.
:::

There are two settings under Admin → Settings → Community Events.
**Require staff approval** is on by default. Turn it off and members' events are
listed as soon as they're submitted, though you can still take any of them down.
**Reminder lead (minutes)** is how long before an event its subscribers get their
DM. Leave it blank for 30.
