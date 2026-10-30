"""Constants, pure helpers and table definitions for the Async Qualifiers admin.

Nothing here touches a service or the ORM. The drill-down's tab names live here
because both ``page``'s loaders and its views key on them; the paging constants
because the bug they exist to prevent is a table built without one.
"""

from typing import Any, Dict, Mapping, Sequence

from application.services.async_qualifier import async_qualifier_rules as rules
from application.utils.duration import format_hms
from application.utils.timezone import format_local_display
from models import AsyncQualifierReviewStatus, AsyncQualifierRunStatus
from theme.qualifier_copy import LIVE_RACE_STATUS_LABELS, REVIEW_LABELS, STATUS_LABELS

__all__ = [
    'BOARD_COLUMNS',
    'BOARD_PAGE',
    'BOARD_TAB',
    'GRANTABLE_STATUSES',
    'GRANT_ACTION',
    'LIVE_TAB',
    'POOLS_TAB',
    'POOL_PERMALINK_PREVIEW',
    'QUEUE_PAGE_SIZE',
    'QUEUE_TAB',
    'REVIEWERS_TAB',
    'RUNS_COLUMNS',
    'RUNS_PAGE',
    'RUNS_TAB',
    'board_rows',
    'enum_value',
    'existing_notes',
    'fmt',
    'live_race_color',
    'other_runs_summary',
    'review_label',
    'run_rows',
    'short_url',
    'status_label',
]


def fmt(dt) -> str:
    return format_local_display(dt) if dt else '—'


def enum_value(value: Any) -> str:
    return value.value if hasattr(value, 'value') else str(value)


GRANTABLE_STATUSES = {'finished', 'forfeit', 'disqualified'}

_FINISHED = AsyncQualifierRunStatus.FINISHED.value
_PENDING = AsyncQualifierReviewStatus.PENDING.value

# Tabs are addressed by name so `state['tab']` survives a rebuild of the detail view.
POOLS_TAB = 'Pools'
LIVE_TAB = 'Live Races'
QUEUE_TAB = 'Review Queue'
RUNS_TAB = 'Runs'
BOARD_TAB = 'Leaderboard'
REVIEWERS_TAB = 'Reviewers'

# Keep a pagination prop: without one Quasar renders every row on one page.
RUNS_PAGE = {'rowsPerPage': 25, 'page': 1}
BOARD_PAGE = {'rowsPerPage': 50, 'page': 1}
QUEUE_PAGE_SIZE = 20
POOL_PERMALINK_PREVIEW = 10

GRANT_ACTION = '''
    <q-btn v-if="props.row.grantable" flat dense icon="restart_alt" color="primary"
           label="Grant reattempt"
           @click="$parent.$emit('grant', props.row)">
        <q-tooltip>Void this run and free its pool slot</q-tooltip>
    </q-btn>
    <q-btn v-if="props.row.reversible" flat dense icon="gavel" color="warning"
           label="Change verdict"
           @click="$parent.$emit('override', props.row)">
        <q-tooltip>Overturn this run&rsquo;s review, with a reason</q-tooltip>
    </q-btn>
'''

RUNS_COLUMNS: Sequence[Dict[str, Any]] = [
    {'name': 'player', 'label': 'Player', 'field': 'player', 'align': 'left',
     'sortable': True},
    {'name': 'pool', 'label': 'Pool', 'field': 'pool', 'align': 'left',
     'sortable': True},
    {'name': 'status', 'label': 'Status', 'field': 'status', 'sortable': True},
    {'name': 'review', 'label': 'Review', 'field': 'review', 'sortable': True},
    # Zero-padded HH:MM:SS, so a lexical sort is chronological.
    {'name': 'claimed', 'label': 'Claimed', 'field': 'claimed', 'sortable': True},
    {'name': 'timed', 'label': 'Timed', 'field': 'timed', 'sortable': True},
    {'name': 'score', 'label': 'Score', 'field': 'score', 'sortable': True},
    {'name': 'actions', 'label': '', 'field': 'actions'},
]

BOARD_COLUMNS: Sequence[Dict[str, Any]] = [
    {'name': 'rank', 'label': '#', 'field': 'rank', 'sortable': True},
    {'name': 'user', 'label': 'Player', 'field': 'user', 'align': 'left',
     'sortable': True},
    {'name': 'actual', 'label': 'Score', 'field': 'actual', 'sortable': True},
    {'name': 'estimate', 'label': 'Estimate', 'field': 'estimate', 'sortable': True},
    # Not sortable: 'filled/total' sorts 2/9 above 10/10.
    {'name': 'slots', 'label': 'Slots', 'field': 'slots'},
]


def status_label(run) -> str:
    """A run's state as a sentence would say it, with an expiry called out.

    ``expired_at`` exists to tell an automatic forfeit from a chosen one and was
    read by no surface, so the two were indistinguishable on screen.
    """
    if getattr(run, 'expired_at', None) is not None:
        return 'Forfeited (ran out of time)'
    value = enum_value(run.status)
    return STATUS_LABELS.get(value, value)


def review_label(run) -> str:
    """A run's review state, or '—' while there is nothing to review yet."""
    if enum_value(run.status) in ('pending', 'in_progress'):
        return '—'
    value = enum_value(run.review_status)
    return REVIEW_LABELS.get(value, value)


def run_rows(runs) -> list:
    """The admin Runs tab's rows — every run, because a forfeit never reaches the
    review queue and this is the only place a mis-clicked one can be found."""
    return [
        {
            'id': run.id,
            'player': run.user.display_name or run.user.username,
            'pool': (run.permalink.pool.name if run.permalink and run.permalink.pool else '—'),
            'status': status_label(run) + (' (voided)' if run.reattempted else ''),
            'review': review_label(run),
            'claimed': format_hms(run.elapsed_seconds),
            'timed': format_hms(run.measured_seconds),
            'score': '' if run.score is None else round(run.score, 1),
            'grantable': (not run.reattempted
                          and enum_value(run.status) in GRANTABLE_STATUSES),
            'reversible': (not run.reattempted
                           and enum_value(run.status) == _FINISHED
                           and enum_value(run.review_status) != _PENDING),
        }
        for run in runs
    ]


def board_rows(entries) -> list:
    """Rank comes from the scoring function rather than from row position.

    Three surfaces used to derive it from an ``enumerate`` index and the REST schema
    had none at all, so a tie rendered 1, 2, 3 in input order. One derivation now,
    shared by everything that shows a board.
    """
    return [
        {'rank': e.rank, 'user': e.username, 'actual': e.actual,
         'estimate': e.estimate, 'slots': f'{e.slots_filled}/{e.slots_total}'}
        for e in entries
    ]


def short_url(url: str, limit: int = 60) -> str:
    url = url or ''
    return url if len(url) <= limit else f'{url[:limit]}…'


def other_runs_summary(tally: Mapping[int, Mapping[str, int]], run) -> str:
    """"Player Two: 2 other runs in this qualifier (1 approved, 1 forfeit)".

    Re-reviewing without seeing a runner's other attempts is how two reviewers
    reach different conclusions about the same person.

    ``tally`` is the per-runner outcome count from
    ``AsyncQualifierService.review_queue_context``, which counts *every* run
    including this one — so this run's own outcome is subtracted here rather than
    filtered in the query, keeping the read a plain grouped count.
    """
    counts = dict(tally.get(run.user_id, {}))
    if not counts:
        return ''
    own = ('voided' if run.reattempted
           else enum_value(run.review_status) if enum_value(run.status) == 'finished'
           else enum_value(run.status))
    if counts.get(own):
        counts[own] -= 1
    counts = {label: n for label, n in counts.items() if n > 0}
    total = sum(counts.values())
    if not total:
        return ''
    breakdown = ', '.join(f'{n} {label}' for label, n in sorted(counts.items()))
    runner = run.user.display_name or run.user.username
    plural = '' if total == 1 else 's'
    return f'{runner}: {total} other run{plural} in this qualifier ({breakdown})'


def existing_notes(run) -> list:
    notes = list(getattr(run, 'review_notes', []) or [])
    return [n.note for n in notes if getattr(n, 'note', '')]


def claim_holder(run) -> str:
    """Who holds a live review claim on this run, or ``''`` for nobody.

    Empty for an expired claim too, so the card and the service agree on whether
    the lock still binds — the worker's sweep runs on a timer, and up to a tick
    can pass between a claim ageing out and the row being cleared.
    """
    if not rules.claim_is_live(getattr(run, 'review_claimed_at', None)):
        return ''
    holder = getattr(run, 'review_claimed_by', None)
    if holder is None:
        return ''
    return rules.display_name(holder)


def live_race_color(status) -> str:
    return {
        'scheduled': 'grey',
        'pending': 'blue',
        'in_progress': 'orange',
        'finished': 'green',
        'cancelled': 'negative',
    }.get(enum_value(status), 'grey')


def live_race_label(status) -> str:
    """A live race's status in words, not the enum value (``in_progress``)."""
    value = enum_value(status)
    return LIVE_RACE_STATUS_LABELS.get(value, value.replace('_', ' ').capitalize())
