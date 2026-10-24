"""A big race folds its racers behind "+N more" on every run sheet."""

from theme.tables.match import MatchTableView
from theme.tables.match_player_run_sheet import _BODY_SLOT, _ITEM_SLOT
from theme.tables.match_run_sheet import _body_slot, _item_slot, arrange_run_sheet, decorate_run_sheet_row

RACERS = [{'name': n} for n in ('vortexofdoom', 'Andy', 'ninjembro', 'tjmaelstrom', 'Eykir')]


def _row(players, **extra):
    return {'id': 1, 'state': 'Scheduled', 'players': players, 'scheduled_at': '', 'scheduled_ts': None, **extra}


class _Table:
    def __init__(self, rows):
        self.rows = rows

    def update(self):
        pass


def test_five_racers_fold_to_three_names_and_a_count():
    row = decorate_run_sheet_row(_row(RACERS))
    assert row['_who_fold'] is True
    assert row['_who_head'] == 'vortexofdoom, Andy, ninjembro'
    assert row['_who_more'] == 2
    assert row['_who'] == 'vortexofdoom, Andy, ninjembro, tjmaelstrom, Eykir'
    assert row['_who_open'] is False


def test_four_racers_stay_listed():
    row = decorate_run_sheet_row(_row(RACERS[:4]))
    assert row['_who_fold'] is False


def test_a_titled_race_leads_with_its_title():
    row = decorate_run_sheet_row(_row(RACERS, title='Round 1 · Heat B'))
    assert row['_who_title'] == 'Round 1 · Heat B'
    assert decorate_run_sheet_row(_row(RACERS[:2], title='Final'))['_who_title'] == ''


def test_opening_a_race_survives_a_rearrange():
    view = object.__new__(MatchTableView)
    view.layout = 'run_sheet'
    view.run_sheet_group = None
    view._folded = {}
    view.table = _Table(arrange_run_sheet([_row(RACERS)]))
    view._toggle_racers(1)
    assert view.table.rows[0]['_who_open'] is True
    assert view._arrange([_row(RACERS)])[0]['_who_open'] is True
    view._toggle_racers(1)
    assert view.table.rows[0]['_who_open'] is False


def test_every_run_sheet_slot_draws_the_toggle():
    for slot in (_body_slot(False), _body_slot(True), _item_slot(False), _item_slot(True), _BODY_SLOT, _ITEM_SLOT):
        assert "toggle_racers" in slot
