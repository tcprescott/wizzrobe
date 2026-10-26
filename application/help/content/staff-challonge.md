---
title: Running a tournament on Challonge
slug: staff-challonge
icon: account_tree
order: 242
feature: CHALLONGE
roles: STAFF
summary: Connecting Challonge, linking a bracket, matching its entrants to members, and moving it through its stages.
---

When a tournament's bracket lives on Challonge, Wizzrobe mirrors it. The bracket
decides who plays whom, players book their own matchups from it, and confirmed
results go back to Challonge by themselves. The Challonge sign-in is on Admin →
Settings → Integrations, and everything about one tournament's bracket is in its ⋮
menu on Admin → Tournaments.

## Connecting the account

Wizzrobe reads and writes brackets through one shared Challonge account. The
Challonge card at the top of Settings → Integrations shows who it's signed in as
and how many API requests it's used this month.

If your brackets belong to a Challonge community (a page like
`speedgaming.challonge.com`), type its subdomain in **Add a community** before you
press **Connect Challonge**, and sign in as one of that community's admins. If you
skip that, Challonge won't show Wizzrobe the community's tournaments.

The card lists every community you've connected with, plus any community a linked
tournament lives on. **Reconnect** asks Challonge about each of them again, with
one approval screen per community, so you only ever type a community once. Remove
one with its ×. You can't remove a community that linked tournaments still use.

If a stage change on Tournaments tells you to reconnect, press **Reconnect**.
Older connections didn't get permission to change a tournament's stage.
**Disconnect**, in the card's ⋮ menu, stops every linked tournament syncing until
someone reconnects, so it asks first.

## Syncing on a timer

Challonge doesn't tell Wizzrobe when something changes, so a result someone
enters on Challonge's own site only shows up after a sync. **Sync automatically**
on the Challonge card re-reads every unfinished linked bracket on the interval you
pick (every 15 minutes up to every 6 hours). It's off until you choose one.
Each sync costs about 2 API requests per bracket, and the card shows roughly how
many a day your setting uses. **Sync bracket** in a tournament's ⋮ still works
any time.

When a match someone booked in Wizzrobe turns out to have been reported on
Challonge already, its row on Match Schedule says **Played on Challonge** and it
shows up under **Needs you**. **Record result** copies Challonge's winner onto the
match and confirms it, with no check-in or start DMs, and nothing goes back to
Challonge. **Call off** cancels it instead. start.gg matches work the same way.

## Linking a bracket

On Admin → Tournaments, open the tournament's ⋮ menu and pick **Link a
bracket…**. Paste the Challonge tournament's URL and press **Link & Sync**. The
same box takes a start.gg event link, and Wizzrobe works out the site from the
link. You can also link from the tournament's edit dialog, under Integrations.

The bracket has to belong to the connected Challonge account or live in one of its
communities. Being an admin on someone else's bracket isn't enough, because
Challonge won't show it to Wizzrobe. Ask its owner to move it into your community.

Linking does three things.

- It imports every entrant and match from the bracket.
- It matches each entrant to a member who's linked that Challonge account on their
  Profile.
- It turns off **Players may request matches**, since a bracket-run tournament only
  schedules the matchups its bracket produces.

Players can't sign up for a linked tournament on the Tournaments tab either. Its
entrants are whoever's in the bracket.

A tournament runs on Challonge or on a native Wizzrobe bracket, never both. If it
already has a native bracket, linking is refused.

## Keeping it in sync

:::snippet admin-challonge-sync
Challonge can't tell Wizzrobe when something changes on its site. Wizzrobe
re-reads the bracket when you link it, when you pick **Sync now** under
**Challonge bracket** in its ⋮ menu, when you change its stage from Wizzrobe, and after it pushes a result. If you
edit the bracket on Challonge's own site, nothing here changes until one of those
happens.
:::

The **Bracket** column (1) on Tournaments shows the site, the bracket's stage,
how many entrants still need a member, and when it last synced. The row's ⋮
menu (2) splits in two: **Tournament** for what Wizzrobe keeps, and **Challonge
bracket** for everything that talks to Challonge. Sync after any change you make
on Challonge.

![Admin → Tournaments with the Bracket column marked 1 showing Challonge and the group stage, and a linked tournament's ⋮ menu marked 2 and open, showing a Tournament group and a Challonge bracket group with Sync now, Finalize groups and start bracket, Match entrants to members (marked 3) and Open on Challonge, then Unlink Challonge bracket](/static/help/staff-challonge/linked.webp "One linked tournament in its group stage, with its ⋮ menu open. **Match entrants to members** (3) is covered below.")

## Matching entrants to members

A player who's linked their Challonge account on their Profile is matched to their
entrant automatically. Anyone added to the bracket by name has no account to match,
and none of their matches can be booked until someone's assigned.

**Match entrants to members…**, under **Challonge bracket** in the tournament's ⋮
menu on Tournaments, lists the bracket's entrants with the unmatched ones first.
While anyone's unmatched, the menu item and the Bracket column both show how many.

| Chip | Means |
|---|---|
| {chip:pending:Not linked} | Nobody is matched. Their matches can't be booked |
| {chip:live:Assigned by hand} | Staff picked the member. Survives every sync |
| {chip:ok:Challonge account} | Matched through the player's own linked account |

Pick the member under **Wizzrobe user** (1) and press **Assign** (2). **Clear** (3)
removes a hand assignment and falls back to whoever has that Challonge account
linked, if anyone. A hand assignment beats a linked account, and the dialog warns
you when it's overriding one.

![The Entrants dialog listing a name-only entrant marked Not linked, two assigned by hand, and two matched through their Challonge accounts; the first Wizzrobe user picker is marked 1, its Assign button 2, and the first Clear button 3](/static/help/staff-challonge/entrants.webp "Unmatched entrants sort to the top.")

At the desk, the quickest fix is usually to have the player link Challonge on their
Profile, then sync the bracket.

## Moving through stages

The tournament's ⋮ menu on Tournaments offers the next stage change the
bracket is ready for, and only that one. Each one asks you to confirm.

| The bracket is | The menu item |
|---|---|
| Not started | **Start tournament**, or **Start group stage** if it has groups |
| Group stage underway | **Finalize groups and start bracket**. Every group match has to be finished first |
| Groups finalized | **Start bracket** |
| Awaiting review | **Finalize tournament** |

Each change re-syncs right away, so the next round's matches are bookable as soon
as it's done. Starting locks the entrant list on Challonge. You can't reset a
bracket from here, because that wipes results. Do it on Challonge.

## Results

When a bracket match is confirmed, the winner goes to Challonge and the bracket
re-syncs, which opens up the next matchups. If a result's wrong, fix it on
Challonge and sync the bracket.

## Unlinking

**Unlink Challonge bracket…**, at the bottom of the tournament's ⋮ menu, asks first,
then removes the mirrored entrants and bracket matches from Wizzrobe. The bracket
on Challonge isn't touched, and linking again re-imports it. Matches that are
already scheduled stay exactly as they are. Unlinking is also how you move a
tournament from Challonge to a native bracket.
