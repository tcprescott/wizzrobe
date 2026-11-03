"""Proctor Station's station lookup: a number in the search box finds the
unfinished matches with someone at that station (Thomas, 2026-10-24)."""

import pytest

from theme.tables.match_run_sheet import (
    _STATION_QUERY,
    PROCTOR_RUN_SHEET_FILTER,
    RUN_SHEET_FILTER,
    _body_slot,
    _item_slot,
    decorate_run_sheet_row,
    register_run_sheet_slots,
    station_key,
)


def _row(state, *stations, **extra):
    players = [{'name': f'Player {i}', 'station': s} for i, s in enumerate(stations, 1)]
    return decorate_run_sheet_row({'id': 1, 'state': state, 'players': players, 'scheduled_at': '', **extra})


class TestStationKey:
    @pytest.mark.parametrize('text, key', [
        ('7', '7'), (' 07 ', '7'), ('Station 7', '7'), ('st. 12', '12'), ('ST12', '12'),
        ('#3', '3'), ('0', '0'),
    ])
    def test_a_number_reads_as_a_station(self, text, key):
        assert station_key(text) == key

    @pytest.mark.parametrize('text', [None, '', 'Bob', '7A', '12:30', 'Player 7'])
    def test_anything_else_is_a_plain_search(self, text):
        assert station_key(text) is None

    def test_the_browser_reads_the_same_pattern(self):
        assert f'/^{_STATION_QUERY.pattern}$/i' in PROCTOR_RUN_SHEET_FILTER


class TestOpenStations:
    def test_an_unfinished_match_lists_its_seated_stations(self):
        assert _row('Started', '07', None)['_stations'] == ['7']
        assert _row('Scheduled', '3', 'Station 4')['_stations'] == ['3', '4']

    @pytest.mark.parametrize('state', ['Finished', 'Confirmed'])
    def test_a_finished_match_is_never_found(self, state):
        assert _row(state, '7', '8')['_stations'] == []

    def test_a_match_played_on_its_bracket_site_is_never_found(self):
        assert _row('Scheduled', '7', played_on_site={'site': 'Challonge'})['_stations'] == []

    def test_each_seat_carries_its_key(self):
        assert [s['key'] for s in _row('Started', '07', None)['_seats']] == ['7', None]


class TestSlots:
    def test_the_proctor_board_uses_the_station_filter_and_others_do_not(self):
        class Table:
            def __init__(self):
                self.slots, self._props = {}, {}

            def add_slot(self, name, template):
                self.slots[name] = template

        proctor, admin = Table(), Table()
        register_run_sheet_slots(proctor, proctor=True)
        register_run_sheet_slots(admin)
        assert proctor._props[':filter-method'] == PROCTOR_RUN_SHEET_FILTER
        assert admin._props[':filter-method'] == RUN_SHEET_FILTER

    def test_the_found_seat_lights_up_on_desktop_and_phone(self):
        assert 'run-sheet__seat--hit' in _body_slot(True)
        assert 'props.row._hit_station' in _item_slot(True)

    def test_a_lookup_keeps_group_headings_on_the_rows_it_keeps(self):
        assert '_group_start: first' in PROCTOR_RUN_SHEET_FILTER
        assert '_folded: false' in PROCTOR_RUN_SHEET_FILTER
