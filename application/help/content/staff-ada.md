---
title: ADA accommodation requests
slug: staff-ada
icon: accessible
order: 248
feature: ADA_ACCOMMODATIONS
roles: STAFF
summary: Working the ADA request queue, what proctors see once a request is arranged, and keeping the notes private.
---

Attendees ask for an ADA accommodation from their Profile, and staff handle the
requests in Admin → Members → ADA requests. The number on that tab is how many
still need you.

## The queue

:::snippet admin-ada-queue
The queue lists open requests, oldest first, with the attendee's details next to
your staff notes. Tick **Show withdrawn** to see requests attendees have taken
back. Press **Update** on a row to set its status and write notes only staff can
read. Once a request is Arranged, proctors see an "ADA:" line on the match. They
never see the notes.
:::

You'll get a Discord DM when a request opens, with a button that brings you
here. The DM names the attendee but never says what they asked for.

On the Members tab, the ADA column shows each person's request status. Tick
**Needs ADA** under **Filters** to show only people with an open request.

![The ADA requests tab listing four requests with their status, the attendee's details and staff notes, one marked Changed since arranged, with Show withdrawn marked 1 and the first row's Update button marked 2](/static/help/staff-ada/queue.webp "The ADA queue, oldest first: **Show withdrawn** (1), and **Update** (2) on each row.")

## Working a request

**Update** on a row opens the request. Set its status and write notes for other
staff:

| Status | Use it when |
|---|---|
| {chip:pending:New} | The attendee has just asked. Nobody has picked it up |
| {chip:live:Acknowledged} | Someone is on it but it isn't in place yet |
| {chip:ok:Arranged} | It's sorted. Proctors are told to check with staff |
| {chip:neutral:Withdrawn} | The attendee took the request back. Only they can reopen it |

The attendee sees the status on their Profile but never your notes, and they
don't get a DM when you change it.

## What proctors see

Once a request is Arranged, every one of the attendee's matches on the Admin →
Schedule and Proctor Station run sheets has an "ADA:" line with their name. Open
the match and its Ready to play? checklist has a *name: ADA accommodation*
button under the players. Staff who tap it see the notes. Proctors only see that
something's been arranged and that they should check with staff. Tournament
admins and crew coordinators don't see any of it.

So write the notes for whoever's on the floor. A line or two on what's been set
up and where is plenty.

## When an attendee changes an arranged request

If the attendee edits their details after you've arranged it, the request stays
Arranged so proctors keep the "ADA:" line. It also gets a
{chip:pending:Changed since arranged} chip, and you get one DM about it.

Open it to see what was arranged next to what they're asking for now. Check the
arrangement still covers it, update your notes if it doesn't, and press **Save
and mark reviewed**.

## Keeping it private

Only this community's staff can read the notes, and the attendee's details never
leave the app. No webhook, API or export to another service carries them. Even
so, the app can't promise privacy. Keep medical and personal details out of the
notes. If you need to talk about it, talk to the attendee directly.
