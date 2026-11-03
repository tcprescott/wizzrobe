"""The Proctor Station run sheet: its groups, the step on each line, and the stations.

The board's premise is that the top of the list is the work and each line's
button is the next thing to press. Both are pure functions over the display
rows, so a regression here is caught without a browser.
"""

from theme.dialog.match_sheet import readiness
from theme.tables.match_run_sheet import (
    _SEATED_WHO,
    _body_slot,
    _item_slot,
    arrange_run_sheet,
    next_step,
    proctor_group,
    seat_line,
    seed_pending,
)

PLAYERS = [{'name': 'Player One', 'station': None}, {'name': 'Player Two', 'station': None}]


def _row(id_, state, ts=None, **extra):
    return {'id': id_, 'state': state, 'players': PLAYERS, 'scheduled_ts': ts,
            'scheduled_at': '', **extra}


def _ids(rows):
    return [r['id'] for r in arrange_run_sheet(rows, proctor_group)]


class TestGroups:
    def test_late_then_ready_then_in_play_then_up_next(self):
        rows = [
            _row(1, 'Scheduled', 900),
            _row(2, 'Started', 100),
            _row(3, 'Checked In', 300),
            _row(4, 'Scheduled', 50, is_overdue=True),
        ]
        assert _ids(rows) == [4, 3, 2, 1]

    def test_earliest_first_within_a_group(self):
        rows = [_row(1, 'Started', 300), _row(2, 'Started', 100), _row(3, 'Started', 200)]
        assert _ids(rows) == [2, 3, 1]

    def test_done_comes_last_and_newest_first(self):
        rows = [
            _row(1, 'Finished', 100),
            _row(2, 'Confirmed', 300),
            _row(3, 'Scheduled', 9999),
        ]
        assert _ids(rows) == [3, 2, 1]

    def test_no_players_sits_below_up_next(self):
        rows = [dict(_row(1, 'Scheduled', 10), players=[]), _row(2, 'Scheduled', 99)]
        assert _ids(rows) == [2, 1]

    def test_each_group_carries_its_count_on_every_row(self):
        rows = arrange_run_sheet([
            _row(1, 'Started', 1), _row(2, 'Started', 2), _row(3, 'Checked In', 3),
        ], proctor_group)
        assert [(r['_group'], r['_group_count'], r['_group_start']) for r in rows] == [
            ('Ready to start', 1, True), ('In play', 2, True), ('In play', 2, False),
        ]

    def test_missing_scheduled_ts_does_not_raise(self):
        assert _ids([_row(1, 'Started'), _row(2, 'Started', 5)]) == [1, 2]


class TestNextStep:
    def test_the_step_follows_the_state(self):
        assert next_step(_row(1, 'Scheduled'))['event'] == 'seat'
        assert next_step(_row(1, 'Checked In'))['event'] == 'start'
        assert next_step(_row(1, 'Started'))['event'] == 'winner'
        assert next_step(_row(1, 'Started', players=PLAYERS + PLAYERS[:1]))['event'] == 'finish'
        assert next_step(_row(1, 'Finished')) is None

    def test_late_check_in_and_start_are_the_primary_buttons(self):
        assert next_step(_row(1, 'Scheduled', is_overdue=True))['primary'] is True
        assert next_step(_row(1, 'Scheduled'))['primary'] is False
        assert next_step(_row(1, 'Checked In'))['primary'] is True

    def test_nothing_to_press_without_players_or_on_racetime(self):
        assert next_step(dict(_row(1, 'Scheduled'), players=[])) is None
        assert next_step(_row(1, 'Checked In', is_racetime=True)) is None


class TestSeedStep:
    """A tournament that rolls seeds puts Roll seed between check-in and Start."""

    def test_roll_comes_before_start(self):
        row = _row(1, 'Checked In', tournament_seed_generator='alttpr')
        assert next_step(row)['event'] == 'roll'
        assert next_step(row)['primary'] is True
        assert next_step(dict(row, generated_seed='https://x/h/abc'))['event'] == 'start'

    def test_a_roll_in_flight_holds_the_button(self):
        row = _row(1, 'Checked In', tournament_seed_generator='alttpr', seed_rolling_since='t')
        step = next_step(row)
        assert step['event'] is None and step['rolling'] is True

    def test_a_failed_roll_offers_the_roll_again(self):
        row = _row(1, 'Checked In', tournament_seed_generator='alttpr', seed_roll_error='boom')
        assert next_step(row)['event'] == 'roll'

    def test_no_generator_goes_straight_to_start(self):
        assert next_step(_row(1, 'Checked In'))['event'] == 'start'

    def test_check_in_still_comes_first(self):
        assert next_step(_row(1, 'Scheduled', tournament_seed_generator='alttpr'))['event'] == 'seat'

    def test_pending_only_while_unplayed(self):
        assert seed_pending(_row(1, 'Scheduled', tournament_seed_generator='alttpr'))
        assert not seed_pending(_row(1, 'Started', tournament_seed_generator='alttpr'))
        assert not seed_pending(_row(1, 'Checked In', tournament_seed_generator='alttpr', is_racetime=True))

    def test_the_seed_line_is_proctor_only_and_outside_the_phone_button(self):
        assert 'run-sheet__seed' in _body_slot(True) and 'run-sheet__seed' not in _body_slot(False)
        slot = _item_slot(True, can_run=True)
        opener = slot.index('run-sheet__card-open')
        assert slot.index('</button>', opener) < slot.index('run-sheet__seed-strip')


class TestSeatLine:
    def test_none_some_and_all_seated(self):
        assert seat_line(_row(1, 'Scheduled')) == 'No stations yet'
        some = [{'name': 'Player One', 'station': 4}, {'name': 'Player Two', 'station': None}]
        assert seat_line(dict(_row(1, 'Scheduled'), players=some)) == 'Player One at 4 · Player Two has none'
        both = [{'name': 'Player One', 'station': 4}, {'name': 'Player Two', 'station': 7}]
        assert seat_line(dict(_row(1, 'Scheduled'), players=both)) == 'Stations 4 and 7'

    def test_a_stage_match_never_asks_for_stations(self):
        assert seat_line(_row(1, 'Scheduled', stage='Main Stage')) == ''
        some = [{'name': 'Player One', 'station': 4}, {'name': 'Player Two', 'station': None}]
        assert seat_line(dict(_row(1, 'Scheduled', stage='Main Stage'), players=some)) == 'Player One at 4'

    def test_the_no_station_tag_skips_stage_matches(self):
        assert 'v-if="s.station || !props.row.stage"' in _SEATED_WHO


class TestSlots:
    def test_the_step_button_needs_the_run_capability(self):
        assert "_next.event" in _body_slot(True, can_run=True)
        assert "_next.event" in _item_slot(True, can_run=True)
        assert "_next.event" not in _body_slot(True, can_run=False)
        assert "_next.event" not in _item_slot(True, can_run=False)

    def test_the_admin_line_is_untouched(self):
        assert "_next" not in _body_slot(False) and "_seats" not in _body_slot(False)
        folded_count = '<template v-if="props.row._folded"> · {{ props.row._group_count }}</template>'
        assert "_group_count" not in _item_slot(False).replace(folded_count, '')

    def test_the_phone_step_is_not_nested_in_the_line_button(self):
        slot = _item_slot(True, can_run=True)
        opener = slot.index('run-sheet__card-open')
        assert slot.index('</button>', opener) < slot.index('run-sheet__step')


class TestProctorChecklist:
    def test_crew_stays_off_the_proctors_checklist(self):
        row = _row(1, 'Scheduled', crew_wanted={'commentators': True},
                   crew_required={'commentators': 2}, commentators=[])
        assert 'crew' in [i['key'] for i in readiness(row)]
        assert 'crew' not in [i['key'] for i in readiness(row, crew=False)]
