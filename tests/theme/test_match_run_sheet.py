"""The admin Schedule run sheet: which group a match lands in, and what its status says."""

import time

from theme.dialog.match_sheet import seed_rollable
from theme.tables.match_run_sheet import (
    CREW_NAMES,
    PROCTOR_RUN_SHEET_COLUMNS,
    RUN_SHEET_COLUMNS,
    _body_slot,
    _item_slot,
    arrange_run_sheet,
    crew_names,
    run_sheet_crew,
    run_sheet_status,
)

PLAYERS = [{'name': 'Player One'}, {'name': 'Player Two'}]


def _row(id_, state, ts=None, **extra):
    return {'id': id_, 'state': state, 'players': PLAYERS, 'scheduled_ts': ts,
            'scheduled_at': '', **extra}


class TestStatus:
    def test_finished_splits_into_three_decisions(self):
        assert run_sheet_status(_row(1, 'Finished', needs_review=True, has_result=True)) == ('Flagged', 'cancelled')
        assert run_sheet_status(_row(1, 'Finished', has_result=False)) == ('No result', 'cancelled')
        assert run_sheet_status(_row(1, 'Finished', has_result=True)) == ('To confirm', 'pending')

    def test_late_only_before_check_in(self):
        assert run_sheet_status(_row(1, 'Scheduled', is_overdue=True)) == ('Late', 'pending')
        assert run_sheet_status(_row(1, 'Checked In', is_overdue=True)) == ('Checked in', 'info')

    def test_a_match_without_players_says_so(self):
        assert run_sheet_status(dict(_row(1, 'Scheduled'), players=[]))[0] == 'Waiting for players'


class TestArrange:
    def test_groups_in_decision_live_next_order_with_one_header_each(self):
        rows = [
            _row(1, 'Scheduled', 300),
            _row(2, 'Started', 200),
            _row(3, 'Finished', 100, has_result=True),
            _row(4, 'Scheduled', 100),
            _row(5, 'Confirmed', 50),
            _row(6, 'Confirmed', 60),
        ]
        arranged = arrange_run_sheet(rows)
        assert [r['id'] for r in arranged] == [3, 2, 4, 1, 6, 5]
        assert [r['_group'] for r in arranged if r['_group_start']] == [
            'Needs a decision', 'Live now', 'Up next', 'Confirmed',
        ]

    def test_names_and_stage_fallbacks(self):
        row = arrange_run_sheet([_row(1, 'Scheduled', time.time(), is_stream_candidate=True)])[0]
        assert row['_who'] == 'Player One vs Player Two'
        assert row['_stage'] == 'Candidate'
        assert row['_day'] == 'Today'

    def test_columns_are_not_sortable(self):
        # The groups are the order; a header sort would scatter them.
        assert not any(c.get('sortable') for c in RUN_SHEET_COLUMNS)


class TestSeedRollable:
    def test_mirrors_the_board_gate(self):
        base = _row(1, 'Scheduled', tournament_seed_generator='alttpr')
        assert seed_rollable(base)
        assert not seed_rollable(dict(base, is_racetime=True))
        assert not seed_rollable(dict(base, players=[]))
        assert not seed_rollable(dict(base, state='Finished'))
        assert not seed_rollable(dict(base, tournament_seed_generator=None))


class TestCrew:
    def _crew(self, **extra):
        row = _row(1, 'Scheduled', crew_wanted={'commentators': True, 'trackers': False},
                   crew_required={'commentators': 2, 'trackers': 0}, **extra)
        return arrange_run_sheet([row])[0]['_crew']

    def test_counts_signups_against_need_and_skips_unused_roles(self):
        crew = self._crew(commentators=[{'approved': True}])
        assert [(c['label'], c['count'], c['short']) for c in crew] == [('Commentary', '1/2', True)]

    def test_full_but_unapproved_says_so(self):
        crew = self._crew(commentators=[{'approved': True}, {'approved': False}])
        assert crew[0]['short'] is False
        assert crew[0]['unapproved'] is True

    def test_lists_names_approved_first_and_marks_who_to_approve(self):
        crew = self._crew(commentators=[{'name': 'B', 'approved': False}, {'name': 'A', 'approved': True}])
        assert [(p['name'], p['note']) for p in crew[0]['people']] == [('A', ''), ('B', 'to approve')]
        assert crew[0]['empty'] is False
        assert self._crew()[0]['empty'] is True

    def test_each_role_has_its_own_column(self):
        from theme.tables.match_run_sheet import RUN_SHEET_COLUMNS
        names = [c['name'] for c in RUN_SHEET_COLUMNS]
        assert 'commentators' in names and 'trackers' in names and 'crew' not in names

    def test_a_started_match_is_not_short(self):
        row = _row(1, 'Started', crew_wanted={'commentators': True},
                   crew_required={'commentators': 2})
        assert arrange_run_sheet([row])[0]['_crew'][0]['short'] is False


    def test_no_need_means_no_needs_crew_but_still_to_approve(self):
        # A match off stage needs no crew (the display row sends zeros).
        row = _row(1, 'Scheduled', crew_wanted={'commentators': True},
                   crew_required={'commentators': 0}, commentators=[{'approved': False}])
        crew = arrange_run_sheet([row])[0]['_crew'][0]
        assert (crew['count'], crew['short'], crew['unapproved']) == ('1', False, True)

class TestReadiness:
    def _items(self, **extra):
        from theme.dialog.match_sheet import readiness
        base = _row(1, 'Scheduled', acknowledgments=[
            {'name': 'Player One', 'acknowledged': True},
            {'name': 'Player Two', 'acknowledged': False},
        ])
        return {i['key']: i for i in readiness(dict(base, **extra))}

    def test_names_who_hasnt_confirmed(self):
        item = self._items()['players']
        assert item['ready'] is False
        assert item['detail'] == "Player Two hasn't confirmed"

    def test_stations_ready_only_when_everyone_has_one(self):
        players = [{'name': 'Player One', 'station': 4}, {'name': 'Player Two'}]
        item = self._items(players=players)['stations']
        assert item['ready'] is False
        assert item['detail'] == 'Player One 4 · Player Two has none'

    def test_no_stage_is_information_not_a_gap(self):
        assert self._items()['stage']['ready'] is None

    def test_seed_and_crew_only_where_the_tournament_uses_them(self):
        items = self._items()
        assert 'seed' not in items and 'crew' not in items
        items = self._items(tournament_seed_generator='alttpr', crew_wanted={'commentators': True},
                            crew_required={'commentators': 2}, commentators=[{'approved': False}])
        assert items['seed']['detail'] == 'Not rolled'
        assert items['crew']['detail'] == '2 more needed, 1 waiting for approval below'

    def test_a_rolling_seed_says_rolling_once(self):
        items = self._items(tournament_seed_generator='alttpr', seed_rolling_since='2026-10-13T15:00:00Z',
                            seed_rolling_label='Rolling… 15:57')
        assert items['seed']['detail'] == 'Rolling… 15:57'
        items = self._items(tournament_seed_generator='alttpr', seed_rolling_since='2026-10-13T15:00:00Z')
        assert items['seed']['detail'] == 'Rolling…'


def test_no_column_points_at_a_list():
    # NiceGUI patches in its own body-cell slot (and logs a warning) for any
    # column whose field holds a list, unaware the body slot draws the cell.
    row = arrange_run_sheet([_row(1, 'Scheduled', commentators=[], trackers=[{'name': 'T'}])])[0]
    for col in RUN_SHEET_COLUMNS + PROCTOR_RUN_SHEET_COLUMNS:
        assert not isinstance(row.get(col['field']), (list, set, tuple)), col['name']


class TestQuickApprove:
    """Approve beside a waiting crew name on the admin run sheet."""

    def _crew(self, state):
        row = _row(1, state, crew_wanted={'commentators': True},
                   commentators=[{'name': 'Fry', 'approved': True, 'id': 7},
                                 {'name': 'tsigma6', 'approved': False, 'id': 8}])
        return run_sheet_crew(row)[0]

    def test_people_carry_their_crew_id(self):
        assert [p['id'] for p in self._crew('Scheduled')['people']] == [7, 8]

    def test_only_unfinished_matches_take_approvals(self):
        assert self._crew('Started')['approvable']
        assert not self._crew('Finished')['approvable']

    def test_no_button_unless_the_board_asks(self):
        assert 'approve_crew' not in CREW_NAMES
        assert CREW_NAMES == crew_names()
        assert 'approve_crew' not in _body_slot(False) + _item_slot(False)

    def test_two_taps_and_the_viewers_gate(self):
        names = crew_names('[3].includes(props.row.tournament_id)')
        assert "$parent.$emit('approve_crew'" in names
        assert 'p._arm ?' in names
        assert '[3].includes(props.row.tournament_id)' in names
        # The note gives way to the button rather than sitting beside it.
        assert 'p.note && !(' in names

    def test_phone_card_stops_being_a_button_when_it_holds_one(self):
        # A button inside the card's button is invalid HTML and the inner click
        # would never land, so the approving board's card is a div whose names
        # are the keyboard's way in.
        assert '<button v-if="!props.row._folded" type="button" class="run-sheet__card"' in _item_slot(False)
        slot = _item_slot(False, approve='true')
        assert '<div v-if="!props.row._folded" class="run-sheet__card"' in slot
        assert 'class="run-sheet__who run-sheet__open"' in slot
        assert "approve_crew" in _body_slot(False, approve='true')
