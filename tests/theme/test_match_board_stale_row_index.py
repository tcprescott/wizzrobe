"""A live row update survives the board reloading while it waits (SGLMAN-2E).

``_update_row`` awaits the match fetch and several per-viewer stamps. A full
reload can replace ``table.rows`` meanwhile, so an index found before the
await may be out of range or point at another match.
"""

from types import SimpleNamespace

from theme.tables.match import MatchTableView


class _Table:
    def __init__(self, rows):
        self.rows = rows
        self.updates = 0

    def update(self):
        self.updates += 1


def _row(match_id, **extra):
    return {'id': match_id, 'state': 'Scheduled', 'scheduled_ts': match_id, 'players': [], **extra}


def _board(rows, on_fetch=None, on_stamp=None):
    view = object.__new__(MatchTableView)
    view.layout = 'table'
    view.row_filter = None
    view.crew_discord_id = None
    view._held_ids = set()
    view.table = _Table(rows)

    async def fetch(match_id):
        if on_fetch:
            on_fetch(view)
        return _row(match_id, stage='Stage 1')

    async def stamp(_rows):
        if on_stamp:
            on_stamp(view)

    async def nothing(*_args, **_kwargs):
        return None

    view.display_service = SimpleNamespace(get_match_for_display=fetch)
    view._restamp_shared_state = nothing
    view._apply_accommodations = stamp
    view._apply_comment_counts = nothing
    view._apply_late_visibility = lambda _rows: None
    view._stage_options = lambda: None
    view._notify_rows_changed = lambda: None
    return view


class TestRowsReplacedMidUpdate:
    async def test_a_shrunk_board_drops_the_update_quietly(self):
        rows = [_row(i) for i in range(1, 85)]
        view = _board(rows, on_fetch=lambda v: setattr(v.table, 'rows', [_row(1)]))

        await view._update_row(84, flash=False)

        assert [r['id'] for r in view.table.rows] == [1]

    async def test_a_reordered_board_updates_the_right_row(self):
        view = _board(
            [_row(1), _row(2), _row(3)],
            on_fetch=lambda v: setattr(v.table, 'rows', [_row(3), _row(2), _row(1)]),
        )

        await view._update_row(3, flash=False)

        assert view.table.rows[0] == _row(3, stage='Stage 1')
        assert [r.get('stage') for r in view.table.rows[1:]] == [None, None]

    async def test_a_reload_during_the_later_stamps_is_caught_too(self):
        view = _board(
            [_row(1), _row(2)],
            on_stamp=lambda v: setattr(v.table, 'rows', [_row(2)]),
        )

        await view._update_row(2, flash=False)

        assert view.table.rows == [_row(2, stage='Stage 1')]
