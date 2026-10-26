"""Once a match starts, the run sheets show its actual start and an end."""

from datetime import datetime, timezone

from application.utils.timezone import to_local
from theme.tables.match_player_run_sheet import _BODY_SLOT, _ITEM_SLOT
from theme.tables.match_run_sheet import _body_slot, _item_slot, arrange_run_sheet, decorate_run_sheet_row

BOOKED = datetime(2026, 10, 24, 20, 0, tzinfo=timezone.utc).timestamp()
STARTED = BOOKED + 14 * 60


def _hhmm(ts):
    return f'{to_local(datetime.fromtimestamp(ts, tz=timezone.utc)):%H:%M}'


def _row(state='Started', **extra):
    return {'id': 1, 'state': state, 'players': [{'name': 'A'}, {'name': 'B'}],
            'scheduled_at': '2026-10-24 20:00', 'scheduled_ts': BOOKED,
            'match_length': 90, **extra}


def test_unstarted_match_keeps_its_booked_time_and_estimates_an_end_with_setup():
    for state in ('Scheduled', 'Checked In'):
        row = decorate_run_sheet_row(_row(state=state))
        assert row['_time'] == _hhmm(BOOKED)
        assert row['_until'] == 'to\u00a0~' + _hhmm(BOOKED + (15 + 90) * 60).replace('\u00a0', ' ')
        assert row['_overrun'] is False


def test_unstarted_match_without_a_length_or_time_has_no_estimate():
    assert decorate_run_sheet_row(_row(state='Scheduled', match_length=None))['_until'] == ''
    assert decorate_run_sheet_row(_row(state='Scheduled', scheduled_ts=None))['_until'] == ''


def test_called_off_match_has_no_estimate():
    assert decorate_run_sheet_row(_row(state='Cancelled'))['_until'] == ''


def test_live_match_shows_actual_start_and_estimated_end():
    row = decorate_run_sheet_row(_row(started_ts=STARTED))
    assert row['_time'] == _hhmm(STARTED)
    assert row['_until'] == 'to\u00a0~' + _hhmm(STARTED + 90 * 60).replace('\u00a0', ' ')


def test_live_match_past_its_estimate_is_flagged():
    long_ago = datetime.now(timezone.utc).timestamp() - 3 * 3600
    assert decorate_run_sheet_row(_row(started_ts=long_ago))['_overrun'] is True
    assert decorate_run_sheet_row(_row(started_ts=STARTED + 10**9))['_overrun'] is False


def test_finished_match_shows_actual_start_and_finish():
    finished = STARTED + 87 * 60
    row = decorate_run_sheet_row(_row(state='Finished', started_ts=STARTED, finished_ts=finished))
    assert row['_until'] == 'to\u00a0' + _hhmm(finished).replace('\u00a0', ' ')
    assert row['_overrun'] is False


def test_no_match_length_means_no_estimate():
    row = decorate_run_sheet_row(_row(started_ts=STARTED, match_length=None))
    assert row['_time'] == _hhmm(STARTED)
    assert row['_until'] == ''


def test_live_rows_sort_by_actual_start():
    early = _row(started_ts=STARTED + 600, id=1)
    late_booked_early_start = _row(started_ts=STARTED, id=2, scheduled_ts=BOOKED + 3600)
    arranged = arrange_run_sheet([early, late_booked_early_start])
    assert [r['id'] for r in arranged] == [2, 1]


def test_every_run_sheet_template_draws_the_end_line():
    for slot in (_body_slot(False), _item_slot(False), _body_slot(True), _item_slot(True), _BODY_SLOT, _ITEM_SLOT):
        assert 'run-sheet__until' in slot


def test_booked_time_is_kept_for_the_match_sheet():
    row = decorate_run_sheet_row(_row(started_ts=STARTED))
    assert row['_booked_time'] == _hhmm(BOOKED)
    assert row['_time'] == _hhmm(STARTED)


def test_twelve_hour_viewer_gets_the_booked_time_in_full():
    from application.timezone_context import hour_cycle_scope, tz_scope

    with tz_scope('UTC'), hour_cycle_scope(True):
        row = decorate_run_sheet_row(_row(state='Scheduled'))
    assert row['_time'] == '8:00\u00a0PM'
