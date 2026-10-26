---
title: Exporting tournament data
slug: staff-exports
icon: download
order: 250
audience: staff
summary: Downloading participants and matches for your tournaments as CSV or JSON, where every export lands, and how long it's kept.
---

Admin → Reports → **Exports** builds a download of your tournaments: who played,
how far they got, and every match. Staff can export any tournament in the
community. A tournament admin sees only the tournaments they run.

## Picking tournaments

Every tournament starts ticked. Untick the ones you don't want, or use **Select
all** and **Clear selection** in the **Actions** menu, then press **Download
ZIP**. **Download JSON only** in the same menu skips the spreadsheets.

Exports build in the background so a big one can't slow the app down for
everyone else. The file downloads in your browser as soon as it's ready, usually
within a few seconds. If you leave the page first, it waits for you under **Your
exports**.

## What's in the ZIP

| File | Holds |
|---|---|
| README.txt | What the files are and the time it was generated. |
| tournaments.json | Everything below in one file, for scripts and other tools. |
| *tournament*/participants.csv | One row per player: name, Discord username and id, bracket name, status, seed, placement, wins and losses. |
| *tournament*/matches.csv | One row per match: round, state, times, stage, seed link, and each player's place, finish time and score. |

Status is **active**, **eliminated**, **dropped** or **finished**, read from the
tournament's bracket (Wizzrobe, Challonge or start.gg). It's blank for a
tournament with no bracket, where wins and losses come from match results
instead. Challonge doesn't share placements, so Placement is blank there.

Times are in UTC. Emails, emergency contacts and check-in details are never
included.

## Your exports

Everything you've exported in the last 3 days is listed under **Your exports**,
including volunteer data ZIPs and the **Export CSV** button on tables. Each one
is deleted 3 days after you asked for it. Nobody else can see or download your
exports, and each tournament export is recorded in the audit log.

You can have one export building at a time, and up to 10 an hour. If several
people are exporting at once you may be asked to wait a minute.

## From a script

The same exports are on the REST API under `/api/exports` and on the Wizzrobe
connector as `export_tournaments`, `list_my_exports` and `get_export`. The JSON
follows a published, versioned schema; the repository has it at
`docs/schemas/tournament-export-v1.schema.json`.
