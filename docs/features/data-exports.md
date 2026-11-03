# Data exports

Every bulk download in Wizzrobe goes through one queue, `DataExport`, and lands on
Admin → Reports → **Exports** under "Your exports" for three days. Two kinds are
built in the background by a worker; the third is a log of the instant table CSVs.

| Kind | Built by | Who | Entry points |
|---|---|---|---|
| `tournaments` | worker | Staff (any tournament), tournament admins (their own) | Reports → Exports picker, `POST /api/exports/tournaments`, MCP `export_tournaments` |
| `volunteers` | worker | `can_manage_volunteers`, `VOLUNTEERS` flag on | Volunteers / Volunteer Shifts → Export data, `POST /api/exports/volunteers`, MCP `export_volunteer_data` |
| `table_csv` | the browser, instantly | whoever pressed it | the **Export CSV** button on every table (`theme/tables/export.py`), recorded after download |

No feature flag. The tournament export holds names, Discord usernames, Discord
ids (strings) and bracket names only: no emails, emergency contacts or check-in
data.

## Lifecycle

```
request_* ──► QUEUED ──(worker claim)──► RUNNING ──► READY ──(3 days)──► deleted
                                            └──────► FAILED ──(3 days)──► deleted
```

- `DataExportService.request_tournaments` / `request_volunteers` check
  permission, then `_queue` applies the limits and inserts a `QUEUED` row. The
  request returns at once; nothing heavy runs in the request.
- `data_export_worker` ticks every 5 s and builds **one** export per tick,
  oldest first, across every community (`next_queued_all` + a conditional
  `claim` so two ticks never build the same row). The build runs inside
  `for_each_tenant_scoped` so tenant and timezone are bound. A tournament export
  reads one tournament at a time and sleeps `PAUSE_BETWEEN_TOURNAMENTS` (0.25 s)
  between them, updating `progress_done`/`progress_total` so the UI can say
  "Building 3 of 16".
- `build()` never raises: any error marks the row `FAILED` with a readable
  `error`. It re-checks which tournaments the requester may export, so an admin
  removed between request and build gets a failure, not the data.
- A row left `RUNNING` for 15 minutes (a restart mid-build) is failed with
  "The server restarted while this export was building".
- Every 10 minutes the worker deletes rows older than `RETENTION` (3 days),
  whatever their state.

### Throttling

| Limit | Value | Error |
|---|---|---|
| Exports queued or building per person | 1 | "You already have an export building…" |
| Exports per person per hour | 10 | "…an hour…" |
| Exports queued or building per community | 5 | "Several exports are building…" |

Logged table CSVs count toward none of them.

## Reading an export

Only the requester can see or download their export; anyone else gets "not
found", including staff. `get_mine`, `list_mine` and `download(actor, id,
fmt)` enforce that. `fmt='json'` returns the tournament export's
`tournaments.json` on its own.

The UI downloads the file automatically in the browser that asked for it
(`theme/exports.watch_until_ready`, polling every 2 s). If the reader leaves
first it waits under "Your exports".

## The tournament ZIP

```
README.txt
tournaments.json
<slug>-<id>/participants.csv
<slug>-<id>/matches.csv
...
```

The CSVs are UTF-8 with a BOM (Excel opens them correctly). `matches.csv` has
`player_N_*` columns, as many as the widest match.

## The JSON format

`tournaments.json` is format `wizzrobe-tournament-export`, `schema_version` 1.
The JSON Schema is [`docs/schemas/tournament-export-v1.schema.json`](../schemas/tournament-export-v1.schema.json),
and `tests/services/test_data_export_service.py` validates a real export
against it. **Adding a field is fine; renaming, removing or changing the
meaning of one bumps `schema_version` and needs a new schema file.**

```json
{
  "format": "wizzrobe-tournament-export",
  "schema_version": 1,
  "generated_at": "2026-10-25T21:04:11Z",
  "community": {"slug": "sgl26", "name": "SpeedGaming Live 2026"},
  "tournaments": [{
    "id": 12, "name": "ALTTPR", "short_name": "ALTTPR", "format": "1v1",
    "active": true, "test": false,
    "bracket": {"site": "challonge", "url": "https://challonge.com/..."},
    "participants": [{
      "player_id": 301, "name": "Runner", "discord_username": "runner",
      "discord_id": "123456789012345678", "bracket_name": "Runner",
      "enrolled": true, "status": "eliminated", "seed": 4,
      "placement": null, "wins": 3, "losses": 2
    }],
    "matches": [{
      "match_id": 88, "source": "wizzrobe", "bracket_site": "challonge",
      "bracket_match_id": "401", "round": "Winners Round 2", "title": null,
      "state": "confirmed", "scheduled_at": "2026-10-23T18:00:00Z",
      "started_at": "...", "finished_at": "...", "confirmed_at": "...",
      "stage": "Main Stage", "seed_url": "https://alttpr.com/h/...",
      "players": [{"player_id": 301, "name": "Runner", "discord_id": "123...",
                   "place": 1, "finish_time_seconds": 5402, "score": 1}],
      "winner_player_id": 301, "winner_name": "Runner"
    }]
  }]
}
```

### Where the numbers come from

`bracket_site()` picks the bracket in this order: start.gg, Challonge, a
Wizzrobe bracket, none.

| Bracket | status | seed | placement | wins / losses |
|---|---|---|---|---|
| Wizzrobe | entry / entrant status (`dropped` from the entrant) | entry seed | `final_rank` | completed bracket matches |
| start.gg | open set → active, last set lost → eliminated, bracket finished → finished | entrant seed | pool standings | completed sets |
| Challonge | same heuristic as start.gg | participant seed | always null (Challonge doesn't share it) | completed matches |
| none | null | null | null | `finish_rank` on finished Wizzrobe matches |

Match rows: every Wizzrobe match in the tournament (`source: wizzrobe`, linked to
its bracket match when there is one), plus every bracket match that was never
scheduled in Wizzrobe (`match_id: null`, `state` `unscheduled` or `reported`).

## Code

| Layer | File |
|---|---|
| Model | `models/data_export.py` (`DataExport`, `DataExportKind`, `DataExportStatus` in `models/enums.py`) |
| Repositories | `data_export_repository.py`, `tournament_export_repository.py` |
| Service | `application/services/exports/data_export_service.py` |
| Builder | `application/services/exports/tournament_export_builder.py` (pure functions + scoped reads) |
| Worker | `application/services/exports/data_export_worker.py` |
| UI | `pages/admin_tabs/reports/exports.py`, `theme/exports.py`, `theme/dialog/volunteer_export_dialog.py`, `theme/tables/export.py` |
| REST | `api/routers/exports.py` |
| MCP | `mcpserver/tools/exports.py` |

Audited as `tournament.data_exported` when a tournament export finishes building;
the volunteer export keeps its existing audit row.
