"""A run sheet's group headers fold their groups.

Done (and the admin board's Confirmed) start folded; a viewer's own choice wins
over that default and survives a held row and a tidy.
"""

from theme.tables.match import MatchTableView
from theme.tables.match_run_sheet import (
    RUN_SHEET_FILTER,
    RUN_SHEET_FILTER_PREFIX,
    _body_slot,
    _item_slot,
    proctor_group,
)


class _Table:
    def __init__(self):
        self.rows = []
        self.updates = 0

    def update(self):
        self.updates += 1


def _view(folded=None):
    view = object.__new__(MatchTableView)
    view.layout = 'run_sheet'
    view.run_sheet_group = proctor_group
    view.table_key = None
    view._folded = folded or {}
    view._held_ids = set()
    view.table = _Table()
    return view


def _row(match_id, state, ts):
    return {'id': match_id, 'state': state, 'scheduled_ts': ts, 'players': [{'name': 'P'}]}


def _folded_groups(rows):
    return {r['_group'] for r in rows if r['_folded']}


class TestDefaults:
    def test_done_starts_folded_and_the_rest_open(self):
        rows = _view()._arrange([_row(1, 'Started', 1), _row(2, 'Confirmed', 2), _row(3, 'Scheduled', 3)])
        assert _folded_groups(rows) == {'Done'}

    def test_the_admin_boards_confirmed_starts_folded(self):
        view = _view()
        view.run_sheet_group = None
        rows = view._arrange([_row(1, 'Confirmed', 1), _row(2, 'Started', 2)])
        assert _folded_groups(rows) == {'Confirmed'}

    def test_a_saved_choice_beats_the_default(self):
        rows = _view({'Done': False, 'In play': True})._arrange(
            [_row(1, 'Started', 1), _row(2, 'Confirmed', 2)])
        assert _folded_groups(rows) == {'In play'}


class TestToggle:
    async def test_folding_reflags_the_rows_in_place(self):
        view = _view()
        view.table.rows = view._arrange([_row(1, 'Started', 1), _row(2, 'Started', 2), _row(3, 'Confirmed', 3)])
        order = [r['id'] for r in view.table.rows]

        await view._toggle_group('In play', None)

        assert _folded_groups(view.table.rows) == {'In play', 'Done'}
        assert [r['id'] for r in view.table.rows] == order
        assert view.table.updates == 1

        await view._toggle_group('Done', None)
        assert _folded_groups(view.table.rows) == {'In play'}

    async def test_a_held_row_folds_with_the_group_it_is_drawn_under(self):
        view = _view()
        view._tidy_event = 'wiz_board_tidy_test'
        view._tidy_pill = type('P', (), {'visible': True, 'set_text': lambda self, t: None})()
        view.table.rows = view._arrange([_row(1, 'Started', 1), _row(2, 'Started', 2)])

        await view._toggle_group('In play', None)
        view._place_in_hold(0, _row(1, 'Finished', 1))

        assert view.table.rows[0]['_group'] == 'In play'
        assert view.table.rows[0]['_folded'] is True


class TestTemplates:
    def test_folded_rows_draw_nothing_but_their_header(self):
        for proctor in (True, False):
            assert 'v-if="!props.row._folded"' in _body_slot(proctor)
            assert 'v-if="!props.row._folded"' in _item_slot(proctor)

    def test_the_header_is_a_button_that_says_whether_it_is_open(self):
        slot = _body_slot(True)
        assert 'class="run-sheet__fold"' in slot
        assert ':aria-expanded' in slot
        assert "$emit('toggle_group', props.row._group)" in slot

    def test_the_filter_keeps_a_folded_groups_header_row(self):
        assert '!row._folded || row._group_start' in RUN_SHEET_FILTER

    def test_the_filter_value_is_a_truthy_string(self):
        from theme.tables.match_run_sheet import run_sheet_terms
        for text in (None, '', 'Bob'):
            assert isinstance(run_sheet_terms(text), str) and run_sheet_terms(text)
        assert run_sheet_terms('Bob') == RUN_SHEET_FILTER_PREFIX + 'Bob'


class TestPlayerRunSheet:
    """Event › Schedule and My Schedule fold the same way, Finished first."""

    def _view(self, folded=None):
        view = _view(folded)
        view.layout = 'player_run_sheet'
        view._discord_id = None
        return view

    def test_finished_starts_folded_and_the_rest_open(self):
        rows = self._view()._arrange([_row(1, 'Started', 1), _row(2, 'Confirmed', 2), _row(3, 'Scheduled', 3)])
        assert _folded_groups(rows) == {'Finished'}
        assert {r['_group']: r['_group_count'] for r in rows} == {'Live now': 1, 'Finished': 1, 'Up next': 1}

    def test_a_saved_choice_beats_the_default(self):
        rows = self._view({'Finished': False, 'Up next': True})._arrange(
            [_row(1, 'Scheduled', 1), _row(2, 'Confirmed', 2)])
        assert _folded_groups(rows) == {'Up next'}

    def test_the_slots_fold_and_the_filter_keeps_headers(self):
        from theme.tables.match_player_run_sheet import register_player_run_sheet_slots

        class Table:
            def __init__(self):
                self.slots = {}
                self._props = {}

            def add_slot(self, name, template):
                self.slots[name] = template

        table = Table()
        register_player_run_sheet_slots(table)
        for slot in table.slots.values():
            assert 'class="run-sheet__fold"' in slot
            assert 'v-if="!props.row._folded"' in slot
        assert table._props[':filter-method'] == RUN_SHEET_FILTER
        assert table._props['filter'] == RUN_SHEET_FILTER_PREFIX
