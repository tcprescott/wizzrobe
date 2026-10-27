---
title: Prize payouts
slug: staff-payouts
icon: payments
order: 246
feature: PAYOUTS
audience: staff
summary: Setting a tournament's prize pool (by hand or from sglive), splitting it by placement, naming the winners, and copying the payout block.
---

:::snippet admin-payouts
Admin → Payouts records what each placement in a tournament wins and builds the
block you paste when the money goes out. Staff see every tournament. A
tournament admin sees only their own.
:::

The table shows each tournament's pool, bonus and total, how many places are
paid, how much of the pool is allocated, and a status:

| Status | Means |
|---|---|
| No split | Nobody has set out the places yet |
| {chip:pending:Winners not named} | The split exists but at least one place has no winner |
| {chip:ok:Ready} | Every place has a winner. Safe to copy |

## The pool

The **Prize pool** button on each row sets the advertised pool and any bonus on
top. Shares come out of the two added together. Leave a field blank until
someone's decided it, because blank and zero mean different things.

Change the pool whenever it moves. Percentage shares are worked out from it every
time, so you don't need to edit anything else. Dollar amounts stay as typed.

## Pools from sglive

If a game has a page on [sglive.speedgaming.org](https://sglive.speedgaming.org),
Wizzrobe can keep its pool up to date for you. Open **Actions › Link sglive
pages**. Every game on sglive is listed with its current total, and you pick the
tournament it pays out for. Names that match are already filled in. Save, and
linked pools fill in straight away, then update every 15 minutes.

A synced pool has a sync icon in the Pool column, and its Prize pool dialog
locks the pool. The bonus stays yours to type. **Unlink** in that dialog stops
the sync and keeps the last amount. **Actions › Update from sglive now** doesn't
wait for the next update.

If sglive is down or drops a game, the pool keeps its last amount.

The event info tournament list shows each tournament's pool and bonus added
together, for any tournament whose total is above zero.

## The split

**Edit split**, in the row's ⋮ menu, has one row per paid place: the place, its
share, and the winner. **Add place** (1) adds a row, and the red bin (2) on a row
removes it.

Each share is a percent or a dollar amount: the **$ | %** switch beside it picks
which. Use dollars when the organizer announced dollars ("1st gets $650"),
because a percentage can only hold two decimals and $650 of $1,490 would come
out as $649.94. Dollar amounts don't move when the pool does. A split can mix
the two, and the greyed text in each box shows the other unit as you type.

Shares can add up to less than the total, and the Allocated line at the bottom
shows how much is placed. They can't add up to more.

For a tie, add two rows with the same place and give each its own share. Two
players tied for third at 10% each are two 10% rows, not one 20% row you split
later.

![The split dialog for a finished tournament: first at 50%, second at 30%, and two third places at 10% each, each with a winner picked, with Add place marked 1 and the first row's red bin marked 2](/static/help/staff-payouts/split.webp "Two third places, each paid their own share.")

You can save the split before the tournament finishes and fill in the winners
later. The winner list has the tournament's entrants, plus anyone already named.

## Copying the payout block

**Copy for Matcherino**, in the row's ⋮ menu, copies the block to your
clipboard, ready to paste wherever you send payouts from. It starts with the
total pool and the bonus, then has one line per place:

| Line | Example |
|---|---|
| A named winner | `1st place - Jemgold (jemgold#100234): 50% / $550.00` |
| No handle on file | `3rd place - greenpotion (no Matcherino handle): 10% / $110.00` |
| Nobody named yet | `3rd place - (unassigned): 10% / $110.00` |
| A dollar amount | `1st place - SpeckySpecks (specky#1234): $650.00` |

Each winner's Matcherino handle comes from their Profile. If a winner hasn't
entered one, the block says so, and you know who to chase before you pay out.

Percentage amounts are rounded to the cent on each row, so the total paid can
come out a cent under the pool.
