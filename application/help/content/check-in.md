---
title: Checking in at the event
slug: check-in
icon: how_to_reg
order: 58
feature: EVENT_CHECK_IN
summary: Checking in when you arrive, and working the check-in desk on your phone: finding people, linking accounts, lanyards, walk-ups, comps and refunds.
---

Checking in tells the event you've arrived, and it's where you pick up your
lanyard. Checking a *match* in is something else. A proctor does that once
you're seated to play.

The first section is for everyone. The rest is for volunteers and staff working
the check-in desk.

## Checking in when you arrive

Go to the check-in desk and give the volunteer the name you bought your badge
under on Matcherino. If they can't find you, show them the badge code from your
Matcherino purchase. The desk searches codes too. If someone else bought your
badge, give their name or the code.

If your Matcherino purchase isn't connected to your account here yet, the
volunteer will ask for your Discord or Twitch name to connect them. You only do
this once. Next event, you're recognised.

Staff, and volunteers scheduled for enough hours, don't need a badge. You're
already on the desk's list, so just give your name.

Once you're checked in, the bot DMs you on Discord to say so. That DM is also a
test. Match calls, crew asks and shift reminders all arrive the same way, so if
it doesn't reach you, the volunteer will ask you to fix it then and there. Join
the community's Discord server and allow DMs from server members, or turn
Discord DMs back on under
[Profile → Notifications](/help/notifications#where-your-settings-live).

## The desk

:::snippet check-in-desk
The desk is Volunteer → Check-in Desk. It shows up while an event is open, for
anyone with the Check In Desk role and for staff. It's built for a phone.

The event name and its sync button sit at the top. Below them, the search box
and the progress bar stay put as you scroll, so the cards get the rest of the
screen. Search takes a name or a badge code. The filter menu beside it (All, Not
yet, Checked in, Comped, Walk-ups) shows a count on each option. The progress bar
reads "212 of 340 checked in · synced 14:05", leaving refunded people out, and the
time says when the roster last came from Matcherino. That matters when it
[goes amber](/help/check-in#when-the-roster-may-be-out-of-date).

Below that, each person gets a card with their name, the account they're linked
to and how, their lanyard, and a big **Check in** button. Someone with no account
shows a **Not linked** button that opens the link dialog. Everything else
(changing or removing a link, undo, the DM retry, removing a walk-up) lives in
the card's ⋮ menu, well away from the button you tap all day. On a screen 1200px
wide or more, the desk is a table with the same buttons on each row.
:::

![The check-in desk on a phone: the search box with the filter menu beside it and the progress bar, above cards for a Base buyer, a volunteer and someone with no badge; on the first card the Not linked button is marked 1, Check in 2 and the ⋮ menu 3](/static/help/check-in/desk-phone.webp "The desk on a phone: **Not linked** (1), **Check in** (2) and the card's ⋮ menu (3).")

## Finding someone

Search matches the name they bought under, the name of their linked account,
their Discord and Twitch usernames, and every badge code they hold. It runs on
your phone, so results show up as you type, even on slow venue wifi.

No match? Try the badge code instead of the name, and make sure the filter menu
is on All. If there's still nothing, they aren't on the roster. They may have
bought after the last sync, or not bought at all. Send them to staff.

## Checking someone in

:::snippet check-in-lanyard
Tap **Check in**. A bar along the bottom shows "Checked in Sam" and, in bold,
what to give them: "Hand over: VIP lanyard". It stays for eight seconds with an
**Undo** button. After that, undo from ⋮ → **Undo check-in**.

Everyone gets one lanyard, the highest they qualify for, each in its own colour:

| Lanyard | Colour | Who gets it |
|---|---|---|
| Staff | Red | Staff comped for this event |
| Volunteer | Purple | Anyone with a published volunteer shift, whether or not they bought a badge |
| VIP | Gold | The best badge they hold is a VIP badge |
| Base | Blue | The best badge they hold is a Base badge |
| Day Pass | Teal | They hold only a Day Pass |

So a staffer who bought a VIP badge still wears Staff.

A note like "+ Base badge" next to the lanyard means they bought more than one
badge, usually one for a friend. The friend isn't on the roster separately, so
hand over a lanyard for each badge.
:::

If two phones tap the same person at once, only one check-in counts. The second
phone says who got there first and when.

## Linking an account

:::snippet check-in-link
When someone's Matcherino purchase isn't linked to an account here, tapping
**Check in** opens Check in *their name* first. Ask for their Discord or Twitch
name, then pick their account from Suggestions or Search members. That links them
and checks them in. **Skip, just check in** checks them in without an account,
and **Cancel** records nothing.

The ten seconds are worth it. Linking is what lets the bot reach them during the
event, and it's remembered, so they're recognised automatically next time. You
can still check in someone with no account here. Skipping is fine.

Most buyers are linked before they reach you, matched by the Discord or Twitch
account they bought with. The card says how:

| Card says | Means |
|---|---|
| *via Discord* / *via Twitch* | They bought with the same Discord or Twitch account they use here |
| *seen before* | Linked at an earlier event |
| *via profile handle* | Their profile's Matcherino handle matches the buyer |
| *linked by staff* | Someone at the desk picked the account by hand |
| **Not linked** | Nobody has matched them yet. Tap it to link them. *· 3 suggestions* means the link dialog has accounts with similar names ready to pick |

If a link is wrong, use ⋮ → **Change linked account**, or **Unlink account** if
they have no account here. Someone unlinked on purpose isn't asked again at
check-in.
:::

![The Check in dialog for a buyer with no linked account: no Suggestions for this one, the Search members box marked 1, and the Skip, just check in button marked 2](/static/help/check-in/link-dialog-phone.webp "Checking in someone whose badge isn't linked yet: search for their account (1), or **Skip, just check in** (2).")

## The Discord DM check

:::snippet check-in-dm
Checking in someone with a linked account sends them a short "you're checked in"
DM. A moment later the card shows whether it got there:

| Card says | What to do |
|---|---|
| *Discord DMs reach them* | Nothing. |
| *Discord won't let us DM them* | Ask them to join the community's Discord server and allow DMs from server members, then ⋮ → **Send the test DM again**. |
| *They've turned Discord DMs off in Wizzrobe* | Ask them to turn DMs on in their Profile, or they'll miss match calls. |
| *Couldn't test their DMs: Discord didn't answer* | That's our problem, not theirs. Try ⋮ → **Send the test DM again** in a minute. |

Fix it while they're standing at the desk. Otherwise nobody notices until they
miss a match call.
:::

![A checked-in card saying Discord won't let us DM them, with the fix to ask for](/static/help/check-in/dm-blocked-phone.webp "A DM that didn't get through.")

## Walk-ups

A walk-up is someone checking in without a Matcherino badge. Only staff can add
one, with the add-person button at the top of the desk next to the sync button.
If you're a desk volunteer, send them to staff.

The **Add walk-up** dialog takes either A member (search for their account) or
Just a name, and checks them in straight away unless you untick **Check them in
now**. The desk doesn't sell badges, so anyone who needs one buys it on
Matcherino.

Walk-ups are the only rows you can remove (⋮ → **Remove walk-up**, staff only,
and it asks first). Everyone else comes from Matcherino and would just come back
on the next sync.

## Comped staff and volunteers

Some people get in without buying a badge. Staff decide who for each event:
everyone with certain roles (usually Staff), plus volunteers whose published
shifts add up to the lowest volunteer comp tier. Those are *scheduled* hours, not
worked ones, since most shifts haven't happened yet when the desk opens.

Comped people are already on the roster, under the Comped filter. Someone comped
who also bought a badge is one person, not two, and still gets the Staff or
Volunteer lanyard.

If a volunteer says they should be comped and they're not on the list, their
shifts probably aren't published yet or don't add up to enough. Send them to
staff or a volunteer coordinator, because the desk can't comp anyone. You also
can't link or unlink comped rows at the desk.

## Refunds and people who can't be checked in

:::snippet check-in-withdrawn
A few cards have no **Check in** button and say why instead:

| Card says | Means |
|---|---|
| *Refunded* | Their badge was refunded on Matcherino |
| *No badge* | Every badge they bought has dropped off the venue's sales |
| *No longer comped* | They were comped and don't qualify any more |

These still show under All, so a search finds them and you can see what
happened. A desk volunteer sees "No badge to check in with. Send them to staff."
and should do exactly that.

Staff see an outlined **Check in anyway** button instead, which asks to confirm.
:::

![A card marked Refunded with no Check in button, saying No badge to check in with. Send them to staff.](/static/help/check-in/refunded-phone.webp "What a desk volunteer sees for a refunded badge.")

## When the roster may be out of date

:::snippet check-in-sync
The roster comes from Matcherino's badge sales and refreshes itself every few
minutes. The time on the progress line says how fresh it is, and a line appears
under it when something needs your attention:

| What you see | Means |
|---|---|
| "212 of 340 checked in · synced 14:05" | All good. |
| "Couldn't reach Matcherino at 14:10, so the roster may be out of date." (in amber) | The last refresh failed. The desk keeps going with the last good roster. |
| "Not synced with Matcherino yet." | The event hasn't pulled its first roster. |
| "Comps only" / "Walk-ups only" | This event has no Matcherino venue, on purpose. |

A stale roster doesn't stop the desk. Every check-in still records, and nobody
already on the list goes missing. What it can't show is anyone who bought a badge
since the last good sync, so send them to staff to be added as a walk-up. Let
staff know the line's gone amber too. It usually means the saved Matcherino login
needs replacing.

The round ↻ button next to the event name pulls a fresh roster right away.
:::

If the event's been closed, the desk says "This event is closed. You can still
record late check-ins." and works as normal.

## Several phones at once

Every phone on the desk sees the same roster. When someone checks a person in on
another phone, that card updates and flashes on yours. A phone that was locked
catches up when you wake it.

If the venue wifi drops, a banner says "No connection" and taps aren't sent until
it clears. Nothing you tapped while offline was recorded, so tap again once the
banner says you're back online.

**Export CSV** at the bottom downloads the whole roster, whatever the filter.
