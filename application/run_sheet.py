"""What a match line on a run sheet says: its status, group, next step, crew and tags.

Pure functions over the row dicts ``MatchDisplayService`` builds. The NiceGUI
boards (``theme.tables.match_run_sheet``, the match sheet) and the app's REST
reads (``AdminScheduleService``) both read them, so the two can't drift.
"""

import re
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from application import pending_actions
from application.services.reports_service import CHECK_IN_TO_START
from application.utils.match_labels import has_undecided, matchup_parts
from application.utils.timezone import format_local_time, to_local, today_local

_DECIDE = (0, 'Needs a decision')
_LIVE = (1, 'Live now')
_NEXT = (2, 'Up next')
_NO_PLAYERS = (3, 'Waiting for players')
_DONE = (4, 'Confirmed')


def run_sheet_status(row: dict) -> tuple[str, str]:
    """``(label, chip tone)`` for a row's status cell.

    One word or two, never the raw state plus a timestamp: "Late" and "No
    result" are what an operator acts on, and neither is a state.
    """
    if held := row.get('racetime_held'):
        return f"Held · {held['label']}", 'pending'
    site = row.get('played_on_site')
    if site:
        if site.get('no_winner'):
            return f"No winner on {site['site']}", 'pending'
        return f"Played on {site['site']}", 'pending'
    state = row.get('state')
    if state == 'Finished':
        if row.get('needs_review'):
            return 'Flagged', 'cancelled'
        if not row.get('has_result'):
            return 'No result', 'cancelled'
        return 'To confirm', 'pending'
    if state == 'Confirmed':
        return ('Forfeit', 'ok') if row.get('forfeit') else ('Confirmed', 'ok')
    if state == 'Started':
        return 'Live', 'live'
    if row.get('crew_hold'):
        return 'Not booked yet', 'neutral'
    if row.get('bracket_link_lost'):
        return 'Bracket link lost', 'pending'
    if row.get('placeholders'):
        return 'Players TBD', 'neutral'
    if not row.get('players'):
        return 'Waiting for players', 'neutral'
    if state == 'Checked In':
        return 'Checked in', 'info'
    if row.get('is_overdue'):
        return 'Late', 'pending'
    return 'Scheduled', 'neutral'


def admin_group(row: dict) -> tuple[int, str]:
    """The admin board's groups: what needs a decision, then the clock."""
    if row.get('played_on_site'):
        return _DECIDE
    state = row.get('state')
    if state == 'Finished':
        return _DECIDE
    if state == 'Confirmed':
        return _DONE
    if state == 'Started':
        return _LIVE
    if not row.get('players'):
        return _NO_PLAYERS
    return _NEXT


_LATE = (0, 'Late, check in now')
_READY = (1, 'Ready to start')
_IN_PLAY = (2, 'In play')
_COMING = (3, 'Up next')
_NO_PLAYERS_YET = (4, 'Waiting for players')
_FINISHED = (5, 'Done')


def proctor_group(row: dict) -> tuple[int, str]:
    """The proctor's groups, in the order the work arrives.

    Late first: players past their time with nobody checked in are the one
    thing in the room that gets worse by the minute. A match already played on
    its bracket site is done as far as the floor is concerned.
    """
    if row.get('played_on_site'):
        return _FINISHED
    state = row.get('state')
    if state in ('Finished', 'Confirmed'):
        return _FINISHED
    if state == 'Started':
        return _IN_PLAY
    if state == 'Checked In':
        return _READY
    if not row.get('players'):
        return _NO_PLAYERS_YET
    if row.get('is_overdue'):
        return _LATE
    return _COMING


FOLDED_BY_DEFAULT = frozenset({_DONE[1], _FINISHED[1], 'Finished', 'Not scheduled yet'})


_NEWEST_FIRST = {_DONE[1], _FINISHED[1]}


def next_step(row: dict) -> Optional[dict]:
    """The lifecycle step a proctor takes next on ``row``, or None.

    ``event`` is the board event the existing handlers already listen for, so
    the line's button and the sheet's reach the same service call.
    """
    if row.get('is_racetime') or not row.get('players') or row.get('played_on_site'):
        return None
    state = row.get('state')
    if state == 'Scheduled' and row.get('placeholders'):
        return None
    if state == 'Scheduled':
        return {'event': 'seat', 'label': 'Check in', 'primary': bool(row.get('is_overdue'))}
    if state == 'Checked In':
        if seed_pending(row):
            if row.get('seed_rolling_since'):
                return {'event': None, 'label': 'Rolling', 'primary': False, 'rolling': True}
            return {'event': 'roll', 'label': 'Roll seed', 'primary': True}
        return {'event': 'start', 'label': 'Start', 'primary': True}
    if state == 'Started':
        if len(row.get('players') or []) == 2:
            return {'event': 'winner', 'label': 'Who won?', 'primary': False}
        return {'event': 'finish', 'label': 'Finish', 'primary': False}
    return None


def seed_pending(row: dict) -> bool:
    """The tournament rolls seeds and this match, still unplayed, has none yet."""
    return bool(
        row.get('tournament_seed_generator') and not row.get('is_racetime')
        and row.get('players') and not row.get('placeholders') and not row.get('generated_seed')
        and row.get('state') in ('Scheduled', 'Checked In')
    )


# Keep in step with the station match in PROCTOR_RUN_SHEET_FILTER's JS.
_STATION_QUERY = re.compile(r'(?:station|st\.?|#)?\s*0*(\d+)', re.IGNORECASE)


def station_key(text: Optional[str]) -> Optional[str]:
    """The number a station lookup or a seat's station reads as, or None."""
    match = _STATION_QUERY.fullmatch(str(text or '').strip())
    return match.group(1) if match else None


def _seats(row: dict) -> list[dict]:
    if row.get('placeholders'):
        return []
    return [
        {'name': p.get('name') or '', 'station': p.get('station') or '',
         'key': station_key(p.get('station')),
         'ada': bool(p.get('ada')), 'ada_note': p.get('ada_note')}
        for p in row.get('players') or []
    ]


def _open_stations(row: dict, seats: list[dict]) -> list[str]:
    """The stations a lookup finds ``row`` at: none once it's finished, since
    the question on the floor is who is at a station now (Thomas, 2026-10-24)."""
    if row.get('played_on_site') or row.get('state') in ('Finished', 'Confirmed'):
        return []
    return [s['key'] for s in seats if s['key']]


def seat_line(row: dict) -> str:
    """Where the players sit, in a few words: the phone line's stand-in for
    the desktop's station tag beside each name."""
    players = row.get('players') or []
    if not players or row.get('is_racetime'):
        return ''
    stations = [(p.get('name') or '', p.get('station')) for p in players]
    seated = [str(s) for _n, s in stations if s]
    missing = [n for n, s in stations if not s]
    on_stage = bool(row.get('stage'))
    if not seated:
        return '' if on_stage else 'No stations yet'
    if on_stage and missing:
        return ' · '.join(f'{n} at {s}' for n, s in stations if s)
    if not missing:
        return f"Stations {', '.join(seated[:-1])} and {seated[-1]}" if len(seated) > 1 \
            else f'Station {seated[0]}'
    return ' · '.join(f'{n} at {s}' for n, s in stations if s) + \
        f" · {', '.join(missing)} {'has' if len(missing) == 1 else 'have'} none"


def _day_label(ts) -> str:
    """'Today', 'Tomorrow', 'Yesterday', or the date, on the viewer's clock."""
    if ts is None:
        return ''
    local = to_local(datetime.fromtimestamp(ts, tz=timezone.utc))
    if local is None:
        return ''
    today = today_local()
    day = local.date()
    if day == today:
        return 'Today'
    if day == today + timedelta(days=1):
        return 'Tomorrow'
    if day == today - timedelta(days=1):
        return 'Yesterday'
    return f'{local:%a} {local.day} {local:%b}'


_CREW_ROLES = (('commentators', 'Commentary'), ('trackers', 'Tracking'), ('broadcasters', 'Broadcast'))


def crew_people(people: list, discord_id=None, *, waiting_note: str = 'to approve',
                role: Optional[str] = None) -> list[dict]:
    """The names in one crew role, approved first, as the boards print them.

    ``me`` marks the viewer, whose name the player board follows with "(you)";
    ``note`` is what an unapproved name carries after it. Given the ``role``
    key, an approval still inside its Undo window reads as approved already.
    """
    if role is not None:
        people = [{**p, 'approved': True} if not p.get('approved')
                  and pending_actions.pending_label(('crew', role, p.get('id'))) else p
                  for p in people]
    ordered = sorted(people, key=lambda p: not p.get('approved'))
    return [{
        'name': p.get('name') or '',
        'approved': bool(p.get('approved')),
        'me': bool(discord_id) and str(p.get('discord_id')) == str(discord_id),
        'note': '' if p.get('approved') else waiting_note,
        'id': p.get('id'),
        'with': p.get('with') or '',
    } for p in ordered]


def run_sheet_crew(row: dict) -> list[dict]:
    """Signups against need, and who they are, per crew role the tournament uses.

    ``short`` marks a role with fewer signups than it needs, while that can
    still be fixed: once a match has started, a gap is history, not a task.
    ``empty`` is the "No signups yet" line, shown on the same terms.
    """
    wanted = row.get('crew_wanted') or {}
    required = row.get('crew_required') or {}
    open_ = row.get('state') in ('Scheduled', 'Checked In')
    crew = []
    for key, label in _CREW_ROLES:
        people = row.get(key) or []
        if not (wanted.get(key) or people):
            continue
        need = required.get(key) or 0
        approved = sum(1 for p in people if p.get('approved'))
        count = f'{len(people)}/{need}' if need else str(len(people))
        crew.append({
            'key': key, 'label': label, 'count': count,
            'short': open_ and len(people) < need,
            'unapproved': open_ and (len(people) >= need > approved if need else len(people) > approved),
            'people': crew_people(people, role=key),
            'empty': open_ and not people,
            'approvable': row.get('state') in ('Scheduled', 'Checked In', 'Started'),
        })
    return crew


RACER_FOLD_AT = 5
RACER_HEAD = 3


def _fold_racers(row: dict, names: list) -> None:
    row['_who_fold'] = len(names) >= RACER_FOLD_AT
    row['_who_head'] = ', '.join(names[:RACER_HEAD])
    row['_who_more'] = max(len(names) - RACER_HEAD, 0)
    row['_who_title'] = (row.get('title') or '') if row['_who_fold'] else ''
    row.setdefault('_who_open', False)


def _hhmm(ts: float) -> str:
    return format_local_time(datetime.fromtimestamp(ts, tz=timezone.utc))


def _until(ts: float, approx: bool) -> str:
    """"to ~12:00 PM" for the line under the time. A narrow phone column breaks
    it before the AM/PM, so "to" holds onto the time and the AM/PM doesn't."""
    return 'to\u00a0' + ('~' if approx else '') + _hhmm(ts).replace('\u00a0', ' ')


def _actual_times(row: dict) -> None:
    """Swap the booked time for the actual start, with the finish under it, or
    while the match runs, start + the tournament's match length as "to ~HH:MM".
    ``_overrun`` marks a live match already past that estimate."""
    started = row['started_ts']
    row['_time'] = _hhmm(started)
    row['_day'] = _day_label(started)
    finished = row.get('finished_ts')
    if finished:
        row['_until'] = _until(finished, approx=False)
    elif row.get('match_length'):
        estimate = started + row['match_length'] * 60
        row['_until'] = _until(estimate, approx=True)
        row['_overrun'] = datetime.now(timezone.utc).timestamp() > estimate


def _planned_end(row: dict) -> None:
    """A match not yet started ends around booked time + setup + match length."""
    if row.get('state') in ('Scheduled', 'Checked In') and row.get('scheduled_ts') and row.get('match_length'):
        estimate = row['scheduled_ts'] + CHECK_IN_TO_START.total_seconds() + row['match_length'] * 60
        row['_until'] = _until(estimate, approx=True)


def decorate_run_sheet_row(row: dict) -> dict:
    """Stamp the display fields the run-sheet templates read. Mutates ``row``."""
    label, tone = run_sheet_status(row)
    row['_status'] = label
    row['_tone'] = tone
    row['_time'] = _hhmm(row['scheduled_ts']) if row.get('scheduled_ts') else ''
    row['_day'] = _day_label(row.get('scheduled_ts'))
    # Before the line swaps _time out: the match sheet still shows the booked time.
    row['_booked_time'], row['_booked_day'] = row['_time'], row['_day']
    row['_until'], row['_overrun'] = '', False
    if row.get('started_ts'):
        _actual_times(row)
    else:
        _planned_end(row)
    names = [p.get('name') or '' for p in row.get('players') or []] + list(row.get('placeholders') or [])
    row['_who'] = ' vs '.join(names) if len(names) == 2 else ', '.join(names)
    parts = matchup_parts([p.get('name') or '' for p in row.get('players') or []], row.get('placeholders') or [])
    row['_who_parts'] = parts if has_undecided(parts) else None
    _fold_racers(row, names)
    if row.get('stage'):
        row['_stage'] = row['stage']
    elif row.get('is_stream_candidate'):
        row['_stage'] = 'Candidate'
    else:
        row['_stage'] = ''
    row['_crew'] = run_sheet_crew(row)
    row['_crew_by'] = {c['key']: c for c in row['_crew']}
    row['_next'] = next_step(row)
    stamp_winners(row)
    row['_seed_pending'] = seed_pending(row)
    row['_seed_line'] = bool(
        (row.get('generated_seed') and row.get('state') not in ('Finished', 'Confirmed'))
        or (row['_seed_pending'] and (row.get('state') == 'Checked In'
                                      or row.get('seed_rolling_since') or row.get('seed_roll_error')))
    )
    row['_seats'] = _seats(row)
    row['_stations'] = _open_stations(row, row['_seats'])
    row['_seat_line'] = seat_line(row)
    row['_ada'] = [p.get('name') or '' for p in row.get('players') or [] if p.get('ada')]
    row['_comments'] = comment_badges(row.get('comment_counts'))
    row['_late'] = late_tags(row)
    return row


def arrange_run_sheet(rows: list, group: Callable[[dict], tuple[int, str]] = admin_group) -> list:
    """Decorate, group and order ``rows``; mark the first row of each group.

    ``group`` maps a row to ``(order, header)``: :func:`admin_group` for the
    schedule board, :func:`proctor_group` for the proctor's. Inside a group the
    order is the clock, earliest first, except the finished groups, where the
    most recent result is the one anyone is still looking for.
    """
    for row in rows:
        decorate_run_sheet_row(row)

    def key(row):
        order, header = group(row)
        ts = row.get('started_ts') or row.get('scheduled_ts') or 0
        return (order, -ts if header in _NEWEST_FIRST else ts)

    arranged = sorted(rows, key=key)
    counts: dict[str, int] = {}
    for row in arranged:
        header = group(row)[1]
        counts[header] = counts.get(header, 0) + 1
    previous = None
    for row in arranged:
        _, header = group(row)
        row['_group'] = header
        row['_group_count'] = counts[header]
        row['_group_start'] = header != previous
        previous = header
    return arranged


def winner_choices(row: dict) -> list[dict]:
    """The two players a live head-to-head can be won by, or none."""
    players = row.get('players') or []
    if (row.get('state') != 'Started' or len(players) != 2 or row.get('placeholders')
            or row.get('is_racetime') or row.get('played_on_site')
            or not all(p.get('id') for p in players)):
        return []
    return [{'id': p['id'], 'name': p.get('name') or ''} for p in players]


def stamp_winners(row: dict) -> None:
    row['_winners'] = winner_choices(row)
    row['_pending_result'] = pending_actions.pending_label(('result', row.get('id')))


def late_tags(row: dict) -> list[dict]:
    """'Player One ~20 min late · said 09:41' per player who told staff they're late.

    ``note`` is what they added, for the tag's hover title; the Needs you inbox
    prints it in full.
    """
    return [{
        'key': entry.get('user_id'),
        'label': f"{entry.get('name')} {entry.get('tag')} late"
                 + (f" · said {entry['said']}" if entry.get('said') else ''),
        'note': entry.get('note') or '',
    } for entry in row.get('late') or []]


def comment_badges(counts) -> list[dict]:
    """The line's comment counts, players first, leaving out a zero.

    ``counts`` is only set on a staff board (``MatchTableView.show_comment_counts``),
    since the staff number includes private notes.
    """
    if not counts:
        return []
    badges = []
    for key, icon, one, many in (
        ('player', 'person', 'player comment', 'player comments'),
        ('staff', 'shield', 'staff comment', 'staff comments'),
    ):
        n = counts.get(key) or 0
        if n:
            badges.append({'key': key, 'icon': icon, 'n': n, 'label': f"{n} {one if n == 1 else many}"})
    return badges


SHEET_CREW_ROLES = (
    ('commentators', 'commentator', 'Commentary'),
    ('trackers', 'tracker', 'Tracking'),
    ('broadcasters', 'broadcaster', 'Broadcast'),
)



def _names(names: list) -> str:
    return names[0] if len(names) == 1 else ', '.join(names[:-1]) + ' and ' + names[-1]


def readiness(row: dict, *, crew: bool = True) -> list[dict]:
    """The match sheet's checklist, one item per thing a match needs.

    ``ready`` is True or False for what a match can't start well without, and
    None for what's only information (no stage just means off stream). Seed and
    crew appear only where the tournament uses them, and crew only when the
    viewer looks after crew (``crew``).
    """
    players = row.get('players') or []
    acks = row.get('acknowledgments') or []
    items = []

    if not players:
        items.append({'key': 'players', 'label': 'Players', 'ready': False, 'detail': 'Not decided yet'})
    else:
        unconfirmed = [a.get('name') or '' for a in acks if not a.get('acknowledged')]
        if not unconfirmed:
            detail = 'Everyone has confirmed'
        elif len(unconfirmed) == len(players) and len(players) == 2:
            detail = f'Neither {unconfirmed[0]} nor {unconfirmed[1]} has confirmed'
        else:
            detail = f"{_names(unconfirmed)} {'hasn' if len(unconfirmed) == 1 else 'haven'}'t confirmed"
        items.append({'key': 'players', 'label': 'Players confirmed', 'ready': not unconfirmed, 'detail': detail})

        stations = [(p.get('name') or '', p.get('station')) for p in players]
        missing = [name for name, station in stations if not station]
        if row.get('stage'):
            if len(missing) < len(stations):
                detail = ' · '.join(f'{name} {station}' for name, station in stations if station)
                items.append({'key': 'stations', 'label': 'Stations', 'ready': None, 'detail': detail})
        else:
            if len(missing) == len(stations):
                detail = 'Not assigned'
            else:
                detail = ' · '.join(f'{name} {station}' for name, station in stations if station)
                if missing:
                    detail += f" · {_names(missing)} {'has' if len(missing) == 1 else 'have'} none"
            items.append({'key': 'stations', 'label': 'Stations', 'ready': not missing, 'detail': detail})

    stage = row.get('_stage') or ''
    items.append({
        'key': 'stage', 'label': 'Stage', 'ready': True if row.get('stage') else None,
        'detail': stage or 'No stage',
    })

    seed = row.get('generated_seed')
    if seed or row.get('tournament_seed_generator'):
        if seed:
            ready, detail = True, ''
        elif row.get('seed_rolling_since'):
            ready, detail = False, row.get('seed_rolling_label') or 'Rolling…'
        elif row.get('seed_roll_error'):
            ready, detail = False, row['seed_roll_error']
        elif row.get('is_racetime'):
            ready, detail = None, 'The race room rolls it'
        else:
            ready, detail = False, 'Not rolled'
        items.append({'key': 'seed', 'label': 'Seed', 'ready': ready, 'detail': detail})

    wanted = row.get('crew_wanted') or {}
    required = row.get('crew_required') or {}
    need = approved = waiting = 0
    for key, _role, _label in SHEET_CREW_ROLES:
        if not wanted.get(key):
            continue
        people = row.get(key) or []
        got = sum(1 for p in people if p.get('approved'))
        need += max((required.get(key) or 0) - got, 0)
        approved += got
        waiting += sum(1 for p in people if not p.get('approved'))
    if crew and any(required.get(k) for k, _r, _l in SHEET_CREW_ROLES if wanted.get(k)):
        if need:
            detail = f'{need} more needed'
            if waiting:
                detail += f', {waiting} waiting for approval below'
        else:
            detail = 'Covered'
        items.append({'key': 'crew', 'label': 'Crew', 'ready': not need, 'detail': detail})
    return items
