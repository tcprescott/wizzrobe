"""The run-sheet layout of a match board: one line per match, grouped by what it needs.

The card-per-match layout put every control on every match — stage select, seed
button, crew chips, stations, the lifecycle button and a pencil — at the same
weight, so a staff member looking for the one match that needed them read all
forty. Here a line carries only what identifies a match and where it stands;
everything you can *do* to it lives in the match sheet (``theme.dialog.match_sheet``)
that opens when the line is tapped.

Rows are arranged server-side (:func:`arrange_run_sheet`) rather than by column
sort: the groups *are* the order, so the columns are not sortable and a group
header rides on the first row of its group (``_group_start``).

A group header is also a button that folds the group (``_folded`` on every row
of it). The folded rows stay in ``table.rows`` and :data:`RUN_SHEET_FILTER`
drops them before Quasar pages, so a folded group costs the pager one line, its
header, rather than leaving pages of nothing behind it.
"""

import re
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from nicegui import ui

from application import pending_actions
from application.services.reports_service import CHECK_IN_TO_START
from application.utils.match_labels import has_undecided, matchup_parts
from application.utils.timezone import format_local_time, to_local, today_local
from theme.busy import slot_busy_template
from theme.matchup import WHO_PARTS_VUE
from theme.tables.match_slots import seed_value_template
from theme.tables.run_sheet_meta import META, META_WITH_STAGE, RAIL, STATUS_WORD, card_head
from theme.tables.run_sheet_tags import comment_badges, late_tags
from theme.tables.run_sheet_winners import stamp_winners, winner_buttons

# Crew columns point at _crew_by, not the lists: NiceGUI patches over any column whose field is a list.
RUN_SHEET_COLUMNS = [
    {'name': 'scheduled_at', 'label': 'Time', 'field': 'scheduled_at', 'align': 'left'},
    {'name': 'match', 'label': 'Match', 'field': 'title', 'align': 'left'},
    {'name': 'stage', 'label': 'Stage', 'field': 'stage', 'align': 'left'},
    {'name': 'commentators', 'label': 'Commentators', 'field': '_crew_by', 'align': 'left'},
    {'name': 'trackers', 'label': 'Trackers', 'field': '_crew_by', 'align': 'left'},
    {'name': 'broadcasters', 'label': 'Broadcasters', 'field': '_crew_by', 'align': 'left'},
    {'name': 'status', 'label': 'Status', 'field': 'state', 'align': 'left'},
]

PROCTOR_RUN_SHEET_COLUMNS = [
    {'name': 'scheduled_at', 'label': 'Time', 'field': 'scheduled_at', 'align': 'left'},
    {'name': 'match', 'label': 'Match', 'field': 'title', 'align': 'left'},
    {'name': 'stage', 'label': 'Stage', 'field': 'stage', 'align': 'left'},
    {'name': 'next', 'label': 'Next step', 'field': 'state', 'align': 'left'},
]

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

# terms must stay a string: QTable deep-watches filter, so an object jumps to page 1 on every row update.
# The prefix exists because Quasar only filters while the filter is truthy.
RUN_SHEET_FILTER_PREFIX = '~'

RUN_SHEET_FILTER = """(rows, terms, cols, cellValue) => {
  const text = String(terms || '').slice(1).toLowerCase();
  return rows.filter((row) => (!row._folded || row._group_start)
    && (!text || cols.some((col) => String(cellValue(col, row)).toLowerCase().includes(text))));
}"""


PROCTOR_RUN_SHEET_FILTER = """(rows, terms, cols, cellValue) => {
  const text = String(terms || '').slice(1).trim();
  const station = text.match(/^(?:station|st\\.?|#)?\\s*0*(\\d+)$/i);
  if (station) {
    const key = station[1];
    const hits = rows.filter((row) => !row._removed && (row._stations || []).includes(key));
    const counts = {};
    hits.forEach((row) => { counts[row._group] = (counts[row._group] || 0) + 1; });
    const seen = new Set();
    return hits.map((row) => {
      const first = !seen.has(row._group);
      seen.add(row._group);
      return Object.assign({}, row, {_group_start: first, _group_count: counts[row._group], _folded: false, _hit_station: key});
    });
  }
  const lower = text.toLowerCase();
  return rows.filter((row) => (!row._folded || row._group_start)
    && (!lower || cols.some((col) => String(cellValue(col, row)).toLowerCase().includes(lower))));
}"""


def run_sheet_terms(text: Optional[str]) -> str:
    """The ``filter`` value :data:`RUN_SHEET_FILTER` reads for search ``text``."""
    return RUN_SHEET_FILTER_PREFIX + (text or '')

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


_CREW_COUNT = r"""<span class="run-sheet__crew" :class="c.short ? 'st-pending' : ''">__LABEL__<b>{{ c.count }}</b><template v-if="c.short"> · needs crew</template><template v-else-if="c.unapproved"> · to approve</template></span>"""

def crew_names(approve: str = 'false') -> str:
    can = f"!p.approved && c.approvable && p.id && {approve}"
    button = ('' if approve == 'false' else
              f"""<q-btn v-if="{can}" dense no-caps unelevated outline color="primary" icon="check"
               class="run-sheet__approve" label="Approve"
               :aria-label="'Approve ' + p.name + ' for ' + c.label.toLowerCase()"
               @click.stop="$parent.$emit('approve_crew', {{ row: props.row, role: c.key, crew_id: p.id }})" />""")
    note = 'p.note' if approve == 'false' else f'p.note && !({can})'
    return ('<span class="run-sheet__people">\n'
            '    <span v-for="(p, i) in c.people" :key="i" class="run-sheet__person" :class="p.approved ? \'\' : \'st-pending\'">\n'
            '        <q-icon :name="p.approved ? \'check_circle\' : \'schedule\'" size="14px" :class="p.approved ? \'st-ok\' : \'st-pending\'" aria-hidden="true" />'
            '<span class="run-sheet__person-name">{{ p.name }}<span v-if="p.me" class="wiz-you"> (you)</span></span>'
            '<span v-if="p.with" class="run-sheet__person-note"> · with {{ p.with }}</span>'
            f'<span v-if="{note}" class="run-sheet__person-note"> · {{{{ p.note }}}}</span>{button}\n'
            '    </span>\n'
            '</span>\n'
            '<span v-if="c.empty" class="wiz-crew-empty st-neutral italic-note">No signups yet</span>')


CREW_NAMES = crew_names()

def _crew_cell(approve: str) -> str:
    return r"""<div v-for="c in (props.row._crew_by[col.name] ? [props.row._crew_by[col.name]] : [])" :key="c.key" class="run-sheet__role">
                """ + _CREW_COUNT.replace('__LABEL__', '') + """
                """ + crew_names(approve) + """
            </div>"""


def _crew_lines(approve: str) -> str:
    return r"""<span v-for="c in props.row._crew" :key="c.key" class="run-sheet__role">
                """ + _CREW_COUNT.replace('__LABEL__', '{{ c.label }} ') + """
                """ + crew_names(approve) + """
            </span>"""

HELD_TAG = """<span v-if="props.row._held_note" class="wiz-held-tag" :class="props.row._removed ? 'wiz-held-tag--removed' : ''">{{ props.row._held_note }}</span>"""

UNTIL_LINE = """<span v-if="props.row._until" class="run-sheet__until" :class="props.row._overrun ? 'st-pending' : ''">{{ props.row._until }}</span>"""

_TIME = """<span v-if="props.row._time" class="run-sheet__time" :class="props.row._status === 'Late' ? 'st-pending' : ''">{{ props.row._time }}</span>
            <span v-else class="run-sheet__day">No time</span>
            """ + UNTIL_LINE + """
            <span v-if="props.row._day && props.row._day !== 'Today'" class="run-sheet__day">{{ props.row._day }}</span>
            """ + HELD_TAG

_WHO_TEXT = ('<template v-if="props.row._who_parts">' + WHO_PARTS_VUE + '</template><template v-else>'
             "{{ props.row._who || (props.row.bracket ? props.row.bracket.name : props.row.title) }}</template>")

RACER_TOGGLE = r"""<span class="run-sheet__more" aria-hidden="true" @click.stop="$parent.$emit('toggle_racers', props.row.id)">{{ props.row._who_open ? 'Show fewer' : '+' + props.row._who_more + ' more' }}</span>"""
_RACERS = r"""<span aria-hidden="true">{{ props.row._who_open ? props.row._who : props.row._who_head }} </span>""" + RACER_TOGGLE + r"""<span class="wiz-sr-only">{{ props.row._who }}</span>"""

WHO = (r"""<template v-if="props.row._who_fold"><template v-if="props.row._who_title">{{ props.row._who_title }}<span class="run-sheet__racers">"""
       + _RACERS + r"""</span></template><template v-else>""" + _RACERS + r"""</template></template><template v-else>"""
       + _WHO_TEXT + "</template>")

COMMENT_BADGES = r"""<span v-if="props.row._comments && props.row._comments.length" class="run-sheet__comments">
    <span v-for="b in props.row._comments" :key="b.key" class="run-sheet__comment-count" :title="b.label"><q-icon :name="b.icon" size="14px" aria-hidden="true" /><span aria-hidden="true">{{ b.n }}</span><span class="wiz-sr-only">{{ b.label }}</span></span>
</span>"""

_HIT_SEATS = r"""<template v-for="(s, i) in props.row._seats.filter((s) => s.key === props.row._hit_station)" :key="'hit' + i"><span class="run-sheet__player">{{ s.name }}<span class="run-sheet__seat run-sheet__seat--hit">Station {{ s.station }}</span></span></template>"""

_SEATED_WHO = r"""<template v-if="props.row._seats.length && (!props.row._who_fold || props.row._who_open)"><template v-for="(s, i) in props.row._seats" :key="i"><span v-if="i" class="run-sheet__vs">{{ props.row._seats.length > 2 ? ', ' : ' vs ' }}</span><span class="run-sheet__player">{{ s.name }}<span v-if="s.station || !props.row.stage" class="run-sheet__seat" :class="s.station ? (s.key && s.key === props.row._hit_station ? 'run-sheet__seat--hit' : '') : 'run-sheet__seat--none'">{{ s.station ? 'Station ' + s.station : 'No station' }}</span></span></template><template v-if="props.row._who_fold"> """ + RACER_TOGGLE + """</template></template><template v-else>""" + WHO + """<template v-if="props.row._hit_station"> · """ + _HIT_SEATS + """</template></template>"""

DESC_LINE = """<span v-if="props.row.description_text" class="run-sheet__desc" :title="props.row.description_text">{{ props.row.description_text }}</span>"""
LATE_TAGS = r"""<span v-for="l in props.row._late" :key="l.key" class="wiz-chip wiz-chip--pending run-sheet__late" :title="l.note || null"><q-icon name="schedule" size="14px" aria-hidden="true" />{{ l.label }}</span>"""
LATE_TAGS += r"""<span v-for="n in (props.row.racetime_unlinked || [])" :key="'rt' + n" class="wiz-chip wiz-chip--pending run-sheet__late" title="The room bot can't invite this player or record their time until they link racetime.gg on their profile"><q-icon name="link_off" size="14px" aria-hidden="true" />{{ n }} not linked</span>"""

_ADA = """<span v-if="props.row._ada.length" class="run-sheet__ada"><q-icon name="accessible" size="16px" aria-hidden="true" />ADA: {{ props.row._ada.join(', ') }}</span>"""

_SEED_LINE = r"""<template v-if="props.row._seed_line"><span v-if="props.row.generated_seed" class="run-sheet__seed" @click.stop><q-icon name="casino" size="16px" aria-hidden="true" /><span class="wiz-sr-only">Seed: </span><span class="run-sheet__seed-value">""" + seed_value_template('props.row.generated_seed') + r"""</span></span>
<span v-else class="run-sheet__seed" :class="props.row.seed_rolling_since ? '' : (props.row.seed_roll_error ? 'st-cancelled' : 'st-pending')"><q-spinner v-if="props.row.seed_rolling_since" size="14px" aria-hidden="true" /><q-icon v-else name="casino" size="16px" aria-hidden="true" /><span>{{ props.row.seed_rolling_since ? (props.row.seed_rolling_label || 'Rolling…') : (props.row.seed_roll_error ? 'Seed failed: ' + props.row.seed_roll_error : 'Seed not rolled' + (props.row.seed_ready_label ? ' · ' + props.row.seed_ready_label : '')) }}</span></span></template>"""

_NEXT_CHIP = STATUS_WORD.replace('<span ', """<span v-if="!['Scheduled', 'Checked in', 'Late'].includes(props.row._status)" """, 1)

_NEXT_BUTTON = winner_buttons("props.row._next && props.row._next.event === 'winner'") + """
<q-btn v-else-if="props.row._next && props.row._next.event && props.row._next.event !== 'winner'" no-caps no-wrap unelevated color="primary" :outline="!props.row._next.primary"
                   class="run-sheet__step" :class="{'wiz-busy-btn': !!props.row._busy}" :disable="!!props.row._busy"
                   :label="props.row._busy ? 'Rolling…' : props.row._next.label"
                   :icon="props.row._busy ? undefined : (props.row._next.event === 'roll' ? 'casino' : (props.row._next.event === 'start' ? 'play_arrow' : undefined))"
                   :aria-label="props.row._next.label + ': ' + (props.row._who || props.row.title)"
                   @click.stop="props.row._next.event === 'roll' && (props.row._busy = true); $parent.$emit(props.row._next.event, { key: props.row.id })">""" + slot_busy_template('props.row._busy') + """</q-btn>
<q-btn v-else-if="props.row._next && props.row._next.rolling" no-caps no-wrap unelevated disable class="run-sheet__step"
       :aria-label="(props.row.seed_rolling_label || 'Rolling…') + ': ' + (props.row._who || props.row.title)">
    <q-spinner size="16px" class="q-mr-sm" aria-hidden="true" />{{ props.row.seed_rolling_label || 'Rolling…' }}
</q-btn>"""


def _next(can_run: bool, chip: bool = True) -> str:
    return ('<span class="run-sheet__next">' + (_NEXT_CHIP if chip else '')
            + (_NEXT_BUTTON if can_run else '') + '</span>')


def fold_header(proctor: bool) -> str:
    """The header's fold button. The admin board counts a group only while
    it's folded, when the count is all that is left of it."""
    count = ' · {{ props.row._group_count }}' if proctor \
        else '<template v-if="props.row._folded"> · {{ props.row._group_count }}</template>'
    return ("""<button type="button" class="run-sheet__fold" :aria-expanded="props.row._folded ? 'false' : 'true'" """
            """@click.stop="$parent.$emit('toggle_group', props.row._group)">"""
            """<q-icon :name="props.row._folded ? 'chevron_right' : 'expand_more'" size="20px" aria-hidden="true" />"""
            """<span>{{ props.row._group }}""" + count + """</span></button>""")


def _body_slot(proctor: bool, can_run: bool = False, approve: str = 'false', run: str = 'false') -> str:
    """Desktop: one row per match, a group header row above the first of each
    group. The Match cell's names are the row's button, so the row is reachable
    and operable from the keyboard; the rest of the row is a mouse convenience."""
    if proctor:
        match = """<span class="run-sheet__name-line"><button type="button" class="run-sheet__open" @click.stop="$parent.$emit('open_match', props.row.id)">
                """ + _SEATED_WHO + """
            </button>""" + COMMENT_BADGES + """</span>
            """ + META + """
            """ + DESC_LINE + _ADA + LATE_TAGS + """
            """ + _SEED_LINE
    else:
        match = """<span class="run-sheet__name-line"><button type="button" class="run-sheet__open" @click.stop="$parent.$emit('open_match', props.row.id)">
                """ + WHO + """
            </button>""" + COMMENT_BADGES + """</span>
            """ + META + """
            """ + DESC_LINE + LATE_TAGS + (winner_buttons(run, inline=True) if run != 'false' else '')
    return """
<q-tr v-if="props.row._group_start" class="run-sheet__group-row">
    <q-td colspan="100%" class="run-sheet__group">""" + fold_header(proctor) + """</q-td>
</q-tr>
<q-tr v-if="!props.row._folded" :props="props" class="run-sheet__row" :class="[""" + RAIL + """, props.row._flash ? 'wiz-row-flash' : '', props.row._removed ? 'wiz-row-removed' : '']" :inert="props.row._removed ? '' : null" :data-wiz-row="props.row.id"
      @click="$parent.$emit('open_match', props.row.id)">
    <q-td v-for="col in props.cols" :key="col.name" :props="props">
        <template v-if="col.name === 'scheduled_at'">
            """ + _TIME + """
        </template>
        <template v-else-if="col.name === 'match'">
            """ + match + """
        </template>
        <template v-else-if="col.name === 'stage'">
            <span v-if="props.row._stage">{{ props.row._stage }}</span>
            <span v-else class="run-sheet__none">None</span>
        </template>
        <template v-else-if="col.name === 'commentators' || col.name === 'trackers' || col.name === 'broadcasters'">
            """ + _crew_cell(approve) + """
        </template>
        <template v-else-if="col.name === 'status'">""" + STATUS_WORD + """</template>
""" + ("""        <template v-else-if="col.name === 'next'">""" + _next(can_run) + """</template>
""" if proctor else '') + """        <template v-else>{{ col.value }}</template>
    </q-td>
</q-tr>
"""


def _item_slot(proctor: bool, can_run: bool = False, approve: str = 'false', run: str = 'false') -> str:
    """Phone: the whole line is one button, the time and state across its top
    and the names under them at full width. The proctor's line is a card
    holding two buttons instead, since the step can't sit inside the button
    that opens the match."""
    head = card_head(_TIME)
    if proctor:
        return """
<div class="run-sheet__item">
    <div v-if="props.row._group_start" class="run-sheet__group">""" + fold_header(proctor) + """</div>
    <div v-if="!props.row._folded" class="run-sheet__card run-sheet__card--split" :class="[""" + RAIL + """, props.row._flash ? 'wiz-row-flash' : '', props.row._removed ? 'wiz-row-removed' : '']" :inert="props.row._removed ? '' : null" :data-wiz-row="props.row.id">
        <button type="button" class="run-sheet__card-open" @click="$parent.$emit('open_match', props.row.id)">
            <span class="run-sheet__main">
                """ + head + """
                <span class="run-sheet__who">""" + WHO + """</span>
                """ + META + """
                <span v-if="props.row._stage || props.row._hit_station || props.row._seat_line" class="run-sheet__meta"><template v-if="props.row._stage">{{ props.row._stage }}</template><template v-if="props.row._hit_station"><template v-if="props.row._stage"> · </template>""" + _HIT_SEATS + """</template><template v-else><template v-if="props.row._stage && props.row._seat_line"> · </template>{{ props.row._seat_line }}</template></span>
                """ + DESC_LINE + _ADA + LATE_TAGS + """
                """ + COMMENT_BADGES + """
            </span>
        </button>
        """ + _next(can_run, chip=False) + """
        <div v-if="props.row._seed_line" class="run-sheet__seed-strip">""" + _SEED_LINE + """</div>
    </div>
</div>
"""
    main = """
        <span class="run-sheet__main">
            """ + head + """
            <span class="run-sheet__who">""" + WHO + """</span>
            """ + META_WITH_STAGE + """
            """ + DESC_LINE + LATE_TAGS + """
            <span v-if="props.row._crew.length" class="run-sheet__crew-list">""" + _crew_lines(approve) + """</span>
            """ + COMMENT_BADGES + (winner_buttons(run, inline=True) if run != 'false' else '') + """
        </span>"""
    if approve == 'false' and run == 'false':
        card = """<button v-if="!props.row._folded" type="button" class="run-sheet__card" :class="[""" + RAIL + """, props.row._flash ? 'wiz-row-flash' : '', props.row._removed ? 'wiz-row-removed' : '']" :inert="props.row._removed ? '' : null" :data-wiz-row="props.row.id"
            @click="$parent.$emit('open_match', props.row.id)">""" + main + """
    </button>"""
    else:
        # Approve and the winner buttons can't nest inside a button, so the card isn't one.
        main = main.replace(
            '<span class="run-sheet__who">' + WHO + '</span>',
            '<button type="button" class="run-sheet__who run-sheet__open" @click.stop="$parent.$emit(\'open_match\', props.row.id)">' + WHO + '</button>',
        )
        card = """<div v-if="!props.row._folded" class="run-sheet__card" :class="[""" + RAIL + """, props.row._flash ? 'wiz-row-flash' : '', props.row._removed ? 'wiz-row-removed' : '']" :inert="props.row._removed ? '' : null" :data-wiz-row="props.row.id"
            @click="$parent.$emit('open_match', props.row.id)">""" + main + """
    </div>"""
    return """
<div class="run-sheet__item">
    <div v-if="props.row._group_start" class="run-sheet__group">""" + fold_header(proctor) + """</div>
    """ + card + """
</div>
"""


def register_run_sheet_slots(table, *, proctor: bool = False, can_run: bool = False,
                             approve: str = 'false', run: str = 'false') -> None:
    """The run sheet's desktop ``body`` and phone ``item`` slots on ``table``.

    ``proctor`` draws the proctor's line: stations beside the names and a count
    on each group, plus the next lifecycle step as a button when ``can_run``.
    Search on the board passes ``search_terms=True`` to :func:`search_input`,
    to keep the prefixed string :data:`RUN_SHEET_FILTER` reads. ``approve`` is
    a Vue expression over ``props.row`` (``MatchBoardAccess.crew_js``) for the
    rows whose waiting crew get an Approve button, and ``run`` the same for
    the admin board's rows whose live head-to-head gets winner buttons.
    """
    table.add_slot('body', _body_slot(proctor, can_run, approve, run))
    table.add_slot('item', _item_slot(proctor, can_run, approve, run))
    use_fold_filter(table, proctor=proctor)


def station_lookup_caption(table, box) -> None:
    """Under the proctor's search: say a number is being read as a station, and
    what an empty board means then, since "no matching records" reads as broken."""
    caption = ui.label().classes('text-caption run-sheet__station-caption')

    def show(value: Optional[str]) -> None:
        key = station_key(value)
        caption.set_text(f'Station {key}, matches not finished yet' if key else '')
        caption.set_visibility(bool(key))
        table._props['no-results-label'] = (
            f'Nobody is at station {key} in a match that is still to finish.' if key
            else 'No matches to show yet.')
        table._props['no-data-label'] = 'No matches to show yet.'
        table.update()

    show(box.value)
    box.on_value_change(lambda e: show(e.value))


def use_fold_filter(table, *, proctor: bool = False) -> None:
    """Filter ``table`` with :data:`RUN_SHEET_FILTER`, so folded groups leave the
    pager; the proctor's with :data:`PROCTOR_RUN_SHEET_FILTER`, which also finds
    a station."""
    table._props['filter'] = run_sheet_terms('')
    table._props[':filter-method'] = PROCTOR_RUN_SHEET_FILTER if proctor else RUN_SHEET_FILTER
