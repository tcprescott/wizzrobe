---
title: Async qualifiers
slug: qualifiers
icon: timer
order: 37
feature: ASYNC_QUALIFIERS
summary: Playing a qualifier seed on your own time, submitting your finish, and how the leaderboard scores you.
---

An async qualifier lets you play qualifying seeds whenever you like within a
window, instead of in a scheduled match. You start a run, play the seed, type in
your finish time, and a reviewer checks it. When the window closes, everyone's
runs are scored against each other on a leaderboard.

## Finding a qualifier

Open Qualifiers in the side menu. The Async Qualifiers page lists every qualifier
your community is running, newest first, with its window (when it opens and
closes) and a description. **Open** takes you into one.

Every Discord message about a run you've started also has a button that takes you
back to the qualifier.

## Pools and runs

A qualifier is split into pools, and each pool holds a set of seeds. You get the
same number of runs in every pool, and your result is the total across all of
them. A slot you never play counts as zero, so skipping a pool costs you.

## Starting a run

:::snippet qualifiers-start
Under Start a run, every pool you can still play has a **Start:** button with
the pool's name, and a count beside it like *1 of 2 runs used*. Pressing it
draws a seed and starts your timer at the same moment. You only see the seed once
the clock's running, so be ready to play before you press it.
:::

![A qualifier page on a phone before any runs: its window, Start a run with a Start: Bonus Pool button marked 1 and its 0 of 2 runs used count marked 2, a Start: Standard Pool button, an empty My runs, and a hidden leaderboard](/static/help/qualifiers/start-phone.webp "Each pool's **Start:** button (1) and how many of its runs you've used (2).")

You can only have one run going at a time in a qualifier. You'll never get a seed
you already have a counting run on, and after a reattempt you get a seed you
haven't played in that pool, unless there are none left. The draw is random, with
one exception. Once some seeds in a pool have been played noticeably more than
others, it hands out one of the least-played ones, so every seed gets enough runs
to set a fair par.

When there's no button, the page tells you why:

| What it says | What to do |
|---|---|
| *This qualifier opens* and a date | Come back then. |
| *This qualifier isn't accepting runs.* | The organisers have switched it off. |
| *This qualifier closed* and a date | Qualifying is over. Your scores and the leaderboard are on the same page. |
| *No pools have been set up yet* | The organisers haven't added seeds. Check back later. |
| *You've used all* … *of your runs in every pool.* | You're done. Wait for your reviews and the close. |
| *You've played every seed available in the pools you have runs left in.* | Tell an organiser, because they need to add more seeds. |

A pool line that says *no unplayed seeds left* means the same thing for that one
pool.

## While your run is going

The page shows a card headed Your run is in progress, with the pool name, a
**Your permalink (open the seed)** link and an Elapsed clock.

Every run has a deadline. The card says *Auto-forfeits at* and a time, with a
countdown under it. That's 12 hours after you started, unless your organisers
set a different limit. Shortly before it (an hour, unless they changed that too)
you'll get a Discord DM with a **Submit or forfeit** button. If the deadline
passes, the run is forfeited for you. It scores zero, the slot is used up, and My
runs shows it as *Forfeited (ran out of time)*. You'll get a DM about it, and its
**See your runs** button opens My runs.

If that happened when it shouldn't have, ask an organiser. They can give you
another attempt, and it doesn't come out of your own reattempts.

## Submitting your time

:::snippet qualifiers-submit
Type your time into **Finish time (H:MM:SS)** with all three parts, so a
52-minute run is `0:52:10`, not `52:10`. The line under the box reads back what
it understood, in words, so you can catch a slip before it counts. If you
recorded the run, paste a link into **VoD URL (optional)**. Then press
**Submit**.
:::

![The Your run is in progress card on a phone: the pool, Your permalink (open the seed), the Elapsed clock, the auto-forfeit time and countdown, then the Finish time (H:MM:SS) box marked 1, the VoD URL box, Forfeit marked 2 and Submit marked 3](/static/help/qualifiers/run-phone.webp "Type your **Finish time** (1), then **Submit** (3). **Forfeit** (2) gives the run up.")

The app keeps its own clock from the moment you pressed Start. It won't take a
time longer than that clock, though it allows two minutes for lag. If your time
is 15 minutes or more shorter than the clock, it asks *Is that the right time?*
before sending. That's normal if you finished a while ago and are only
submitting now, so go ahead. If you dropped a digit, choose **Let me fix it**.
Reviewers see both your time and the app's clock.

Once you've submitted, the run shows *Awaiting review* and you can start the next
one straight away. A run waiting for review only holds its own pool slot, so you
don't have to wait for the verdict.

## Forfeiting

**Forfeit** is next to Submit and asks before it does anything. A forfeited run
scores zero, uses up the pool slot, and can't be undone. If you have reattempts
left, the confirmation tells you.

## Reviews

A reviewer approves or rejects each run you submit, and you get a Discord DM
either way, with a **See your runs** button that opens My runs. A rejection
always comes with the reviewer's reason, which also shows in the Reviewer note
column of My runs. A rejected run counts as zero and still uses its slot.

Reviewers can change a verdict later. If one does, the DM says the earlier result
changed, so you're not left with two messages that disagree. Nobody can review
their own run.

## My runs

Every run you've started is listed under My runs, with the seed you played, so
you can quote it if you disagree with a verdict.

| Column | What it shows |
|---|---|
| Status | *Running*, *Finished*, *Forfeited*, *Forfeited (ran out of time)*, *Disqualified* (only from a live race), or *Waiting for the race* for a live race that hasn't started |
| Review | *Awaiting review*, *Approved*, *Rejected*, or *Voided* for a run that no longer counts. Blank while a run is going or waiting for its race |
| Against par | While the window is open: *Under par*, *Near par* or *Above par*, or *Forfeited* or *Disqualified* for a run that didn't finish |
| Score | Replaces Against par once the qualifier closes |
| Reviewer note | The reason behind a verdict, or why a run was voided |

While the qualifier's open you see a band instead of a number. The exact score,
together with your own time, would give away the seed's par, and par stays
hidden while other people are still running. *Under par* means at least 5% faster
than par, and *Above par* means more than 5% slower. A forfeit has no time, so
that column says *Forfeited* (or *Disqualified*) instead.

![My runs on a phone, one card per run: a live-race run Waiting for the race, a Running run, a Rejected run with its Reviewer note marked 1 and its Reattempt button marked 2, and an Approved run marked Near par](/static/help/qualifiers/my-runs-phone.webp "A rejected run's **Reviewer note** (1), and **Reattempt** (2) to replace it.")

## Reattempts

:::snippet qualifiers-reattempt
If a qualifier allows reattempts, My runs shows how many you have left, and each
finished or forfeited run has a **Reattempt** button. Using one voids that run,
so its score is thrown away and it stops counting. That frees its pool slot for a
fresh draw, which gives you a seed you haven't played unless the pool has none
left. You have to give a reason. You can't get the voided run back, so only use a
reattempt on a run you'd rather replace.
:::

Reattempts are only offered while the window is open. A reviewer can also void a
run for you, say for a bad seed or a mis-clicked forfeit, without touching your
own allowance. You'll get a DM with their reason, and My runs shows *Voided by a
reviewer* next to it.

## Scoring and the leaderboard

:::snippet qualifiers-scoring
Each seed has a par, which is the average of the fastest approved times on it
(the five fastest, unless the organisers changed that). Finishing at par scores
100. Every percent slower than par costs a point, so twice par scores 0, and the
best possible score is 105. Forfeits, rejected runs and unplayed slots score 0.
:::

Par moves as more runs are approved, so your score can still change after you've
finished, when someone else's run on the same seed gets approved.

The Leaderboard stays hidden until the qualifier closes, either at its close time
or when the organisers switch it off. A qualifier with no close time stays hidden
until they do. Once it's open:

- Score is your total, with unplayed slots counting zero. Ranking goes by Score,
  and tied players share a rank.
- Estimate fills your unplayed slots with your own average, so a player who
  didn't finish every pool isn't undersold.
- Slots shows how many of your slots you used, out of the total.

The board can run to hundreds of players, so use **Find a player…** to jump to
your own line.

## Live races

Organisers can also run a pool's seed as a live race on racetime.gg, where
everyone plays it at once in one room. Your result comes straight from the room
and counts as approved, with no review. It uses one of your runs in that pool.
Until the race starts, My runs lists it as *Waiting for the race*, and you can
still start a run of your own in the meantime. If you'd already used all your
runs, the result stays in your history but doesn't count.

The room only knows your racetime.gg account, so link it under Connected accounts
on your Profile before you race, or your result can't be matched to you. If your
result goes missing anyway, tell an organiser. They can type it in by hand, as
long as you've joined the community. Some seeds are kept for live races and are
never drawn when you start a run yourself.

## What happens next

When the window closes, My runs switches to exact scores and the leaderboard
opens. What a placing gets you is up to your organisers, so keep an eye on their
announcements for how the qualifier feeds into the event.
