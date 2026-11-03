---
title: Running an async qualifier
slug: staff-qualifiers
icon: timer
order: 245
feature: ASYNC_QUALIFIERS
roles: STAFF, QUALIFIER_ADMIN
summary: Setting up a qualifier and its seed pools, working the review queue, fixing runs, and recording live races.
---

Async qualifiers live on Admin → Qualifiers, under Online play. Staff and anyone
with the Qualifier Admin role can create them, fill their pools and review runs.
A qualifier's own reviewers (see [Leaderboard and
reviewers](#leaderboard-and-reviewers)) get the same tab, showing only the
qualifiers they review. The player's side is in [Async
qualifiers](/help/qualifiers).

## Creating a qualifier

**New Qualifier** opens a dialog where you fill in:

- Name, and a Description players see on the qualifier list.
- Feeds event (optional), a label for the event this qualifies people for. It
  shows on the admin card, not to players.
- The window, as Opens date and time and Closes date and time, read on your own
  clock. Leave a date blank for no limit on that side.
- Runs per pool, which is the same for every pool, and Allowed reattempts, the
  number of runs each player can void and replay themselves.
- Active. Leave it off if players shouldn't see the qualifier yet, and turn it on
  later with **Edit qualifier**.

Set a close date. Results, pars and the leaderboard stay hidden from players
until the qualifier closes. Without a close date it only closes when you switch
Active off with **Edit qualifier** in its ⋮ menu.

A run has 12 hours before it's forfeited automatically, and the runner gets a
warning an hour before. Those two settings, how many times make up a par (five),
and how uneven a pool's play can get before the draw evens it out are all set
through the API, not on this page.

## Pools and seeds

:::snippet qualifiers-pools
Choose **Manage** on a qualifier, then the Pools tab. **Add Pool** takes a name
and, if you want, a seed preset. Fill a pool with **Roll**, which rolls up to 25
seeds from its preset while you wait, or **Add permalinks**, which takes one
http(s) URL per line. Any pasted line that isn't a link gets skipped. The
notification names each one, and the dialog keeps just those lines so you can
fix them.
:::

![Admin → Qualifiers with Dev Async Qualifier's Manage button marked 1, and below it the Pools tab, marked 2, open: Add Pool, then a card per pool with its permalink count, preset, Add permalinks or Roll (the Standard Pool's Roll marked 3), and each seed with its async or live badge and ⋮ menu](/static/help/staff-qualifiers/pools.webp "**Manage** (1), the **Pools** tab (2), and **Roll** (3) on a pool with a preset.")

Check every seed before the window opens. A player's slot is used the moment they
draw a seed, so a broken link costs them a run, and the only fix is giving them a
reattempt.

You pick a pool's preset when you create it, and you can't change it from this
page afterwards. Randomizers that roll seeds in the background can't fill a pool
yet. The pool card says *Cannot roll* with the reason, so use Add permalinks for
those.

Each seed has its own ⋮ menu with **Edit permalink** and **Remove permalink**.
Edit permalink has a **Live race only** switch. A seed with it on is never drawn
when a player starts a run, and is kept for a live race. The par badge on each
seed is its current par, and it moves as runs are approved.

## Working the review queue

:::snippet qualifiers-review-queue
The Review Queue tab holds every submitted run still waiting for a verdict, and
the tab label shows the count. Each card shows the runner's time, the app's own
clock for the run (*Timed*), when it started and finished, the seed played, their
VoD, their other runs in this qualifier, and any earlier notes. **Claim** a card
before you watch the VoD so nobody else picks it up, then **Approve** or
**Reject**. A rejection needs a reason, and the runner gets exactly what you
type.
:::

![The Review Queue tab, marked 1 and labelled with its count of 2, showing two runs from Player Four, each with the claimed time, pool, Claimed and Timed times, the seed played and the runner's other runs; the first card's Claim button is marked 2 and its orange drift badge 3](/static/help/staff-qualifiers/review-queue.webp "**Review Queue** (1): **Claim** a run (2) before you watch it. A *drift* badge (3) flags a time well under the app's clock.")

If someone else has claimed a card, it shows their name and only offers
**Release**. Any reviewer can release any claim, and a claim left alone for two
hours is released automatically.

An orange *drift* badge means the runner's time is 15 minutes or more under the
app's clock and they confirmed it anyway. That's normal when someone submits long
after finishing. Otherwise it's worth checking the VoD.

You can't review your own run. Every verdict recalculates that seed's par and
rescores every approved run on it, so the leaderboard can shift each time.

Once the oldest run in the queue has waited six hours, the qualifier's reviewers
get a Discord DM with a button to this tab. If the qualifier has no reviewers of
its own, everyone with the Qualifier Admin role gets it. It repeats at most once
a day while the backlog lasts.

## Fixing a run

The Runs tab lists every run in the qualifier, including forfeits, which never
reach the queue. **Find a player or pool…** narrows it down. *Claimed* is the
time the runner typed and *Timed* is the app's clock.

- **Grant reattempt** voids a finished or forfeited run and frees its pool slot.
  It doesn't use the runner's own allowance, and it works after the qualifier has
  closed. Use it for a bad seed, a mis-clicked forfeit or a run the clock
  forfeited unfairly. The runner gets a DM with your reason.
- **Change verdict** overturns an approval or a rejection. It needs a reason,
  and the runner is told their earlier result changed.

## Live races

The Live Races tab runs one of a pool's seeds as a race on racetime.gg, with
every entrant playing at once. **New Live Race** takes a title, a pool and,
optionally, the permalink. **Open room** opens the room with your community's
racetime bot.

When the race finishes, each entrant's result is recorded as an approved run with
no review, and the seed's par is recalculated. If a racer has already used all
their runs in that pool, the result stays in their history but doesn't count.

Each race card shows its status: *Scheduled*, *Room open*, *Racing*, *Finished*
or *Cancelled*.

Two things stop a result from landing. A race with no permalink can't be scored,
so the card offers **Set permalink** until it has one. And a racer whose
racetime.gg account isn't linked to anyone here shows on the card as not
recorded. Only the racer can link it, under Connected accounts on their own
Profile, so ask them to before the next race. For this one, use **Record
results** to type in their outcome and time. Its Racer picker lists members of
your community, so a racer who hasn't signed in and joined yet won't be there.
Record results is also how you fix it when racetime finished someone without a
time, or the room's result never arrived.

## Leaderboard and reviewers

The Leaderboard tab shows the board while the qualifier's still open, which
players can't see. It has the same Score, Estimate and Slots columns they get
once it closes.

The Reviewers tab is the qualifier's own reviewer list. The backlog DM goes to
these people, and a reviewer marked *no Discord* won't get it. Staff and
Qualifier Admins can review every qualifier whether they're listed or not.

Anyone listed here without the Qualifier Admin role gets the Admin link and
Admin → Qualifiers, showing just the qualifiers they review, so the backlog DM's
button lands on their queue. They can't create a qualifier, because **New
Qualifier** isn't shown to them.

## Deleting a qualifier

**Delete qualifier** in the card's ⋮ menu removes every pool, seed and run in it.
It asks you to type a phrase first, and it can't be undone.
