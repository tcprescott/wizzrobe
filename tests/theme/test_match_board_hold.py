"""A run sheet holds its rows still until the viewer tidies it.

docs/style-guide.md#hold-still: a match that now belongs in another group keeps
its place and says where it belongs, and the Tidy up pill counts what is held.
"""

from types import SimpleNamespace

from theme.tables.match import MatchTableView
from theme.tables.match_run_sheet import proctor_group


class _Table:
    def __init__(self, rows):
        self.rows = rows


class _Pill:
    def __init__(self):
        self.visible = False
        self.text = ''
        self.scripts = []
        self.client = SimpleNamespace(run_javascript=self.scripts.append)

    def set_visibility(self, visible):
        self.visible = visible

    def set_text(self, text):
        self.text = text


def _board(*rows):
    view = object.__new__(MatchTableView)
    view.layout = 'run_sheet'
    view.run_sheet_group = proctor_group
    view._held_ids = set()
    view._tidy_event = 'wiz_board_tidy_test'
    view._tidy_pill = _Pill()
    view.table = _Table(view._arrange([dict(r) for r in rows]))
    return view


def _row(match_id, state, ts, **extra):
    return {'id': match_id, 'state': state, 'scheduled_ts': ts, 'players': [], **extra}


class TestPlaceInHold:
    def test_a_row_that_changes_group_stays_put_and_says_where_it_belongs(self):
        view = _board(_row(1, 'Scheduled', 100), _row(2, 'Scheduled', 200))
        before = [(r['id'], r['_group']) for r in view.table.rows]

        view._place_in_hold(1, _row(2, 'Started', 200))

        assert [(r['id'], r['_group']) for r in view.table.rows] == before
        assert view.table.rows[1]['_held_note'].startswith('Now ')
        assert view._held_ids == {2}
        assert view._tidy_pill.visible
        assert view._tidy_pill.text == '1 change held · Tidy up'
        assert 'wiz_board_tidy_test' in view._tidy_pill.scripts[0]

    def test_a_change_that_keeps_the_order_holds_nothing(self):
        view = _board(_row(1, 'Scheduled', 100), _row(2, 'Scheduled', 200))

        view._place_in_hold(0, _row(1, 'Scheduled', 100, stage='Stage 1'))

        assert view.table.rows[0]['stage'] == 'Stage 1'
        assert view.table.rows[0]['_held_note'] is None
        assert not view._tidy_pill.visible


class TestRemovedAndRelease:
    def test_a_removed_row_keeps_its_slot_and_the_tidy_clears_the_hold(self):
        view = _board(_row(1, 'Scheduled', 100), _row(2, 'Scheduled', 200))

        view._hold_removed(0)
        view._hold_removed(0)

        assert [r['id'] for r in view.table.rows] == [1, 2]
        assert view.table.rows[0]['_held_note'] == 'Removed'
        assert [r['id'] for r in view._visible_rows()] == [2]
        assert view._tidy_pill.text == '1 change held · Tidy up'

        view._release_hold()

        assert not view._tidy_pill.visible
        assert view._held_ids == set()
