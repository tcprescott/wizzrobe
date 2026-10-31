"""A filtered match board keeps its own row set as changes arrive.

``row_filter`` is not the operator's State chip — it defines what the board *is*.
The tournament-room view shows rolled seeds for unfinished matches, so two live
transitions have to work in opposite directions: a match that finishes leaves the
screen, and a match that gets a seed appears on it. The second is the subtle
one, because rolling a seed publishes ``changed`` (not ``CREATED``) for a match
the board has never had a row for, which a plain in-place update would no-op.

Neither moves the board under the viewer (docs/style-guide.md#hold-still): the
finished match fades in place and the newly seeded one waits for the next tidy.
"""

import asyncio
from types import SimpleNamespace

from application.events import match_live
from application.services.match.match_display_service import is_room_seed_row
from theme.tables.match import MatchTableView


class _Table:
    def __init__(self, rows):
        self.rows = rows
        self.updated = 0

    def update(self):
        self.updated += 1


def _view(rows, *, fetched=None):
    """A MatchTableView with only the pieces these paths touch."""
    view = object.__new__(MatchTableView)
    view.table = _Table([dict(r) for r in rows])
    view.layout = 'table'
    view._held_ids = set()
    view._tidy_event = 'wiz_board_tidy_test'
    view._tidy_pill = _Pill()
    view.row_filter = is_room_seed_row
    view.on_rows_changed = None
    view.on_set_stage = None
    view.show_accommodations = False
    view._row_update_lock = asyncio.Lock()
    view.columns = []
    view.refreshed = 0
    # The row-flash timer needs a live slot context, which none of these paths has.
    view._schedule_flash_clear = lambda _match_id: None

    async def _refresh(*_args):
        view.refreshed += 1

    view.refresh = _refresh
    view.display_service = SimpleNamespace(
        get_match_for_display=_returning(fetched),
    )
    return view


class _Pill:
    def __init__(self):
        self.visible = False
        self.text = ''
        self.client = SimpleNamespace(run_javascript=lambda _js: None)

    def set_visibility(self, visible):
        self.visible = visible

    def set_text(self, text):
        self.text = text


def _returning(value):
    async def _get(_match_id):
        return value
    return _get


SEEDED = {'id': 1, 'generated_seed': 'https://alttpr.com/h/abc', 'state': 'Scheduled'}
STARTED = {'id': 1, 'generated_seed': 'https://alttpr.com/h/abc', 'state': 'Started'}
FINISHED = {'id': 1, 'generated_seed': 'https://alttpr.com/h/abc', 'state': 'Finished'}
UNSEEDED = {'id': 2, 'generated_seed': '', 'state': 'Scheduled'}


class TestUpdateRow:
    async def test_a_match_that_finishes_fades_where_it_sits(self):
        view = _view([SEEDED, UNSEEDED], fetched=FINISHED)

        await view.update_row_by_id(1)

        assert [r['id'] for r in view.table.rows] == [1, 2]
        assert view.table.rows[0]['_removed'] is True
        assert view._visible_rows() == [UNSEEDED]
        assert view._tidy_pill.visible

    async def test_a_row_that_still_qualifies_updates_in_place(self):
        refreshed = dict(SEEDED, stage='Stage 1')
        view = _view([SEEDED], fetched=refreshed)

        await view.update_row_by_id(1)

        assert view.table.rows == [refreshed]


class TestRemoteChange:
    async def test_a_seed_rolled_for_an_unlisted_match_waits_for_a_tidy(self):
        view = _view([SEEDED], fetched=None)

        await view._on_remote_change(99, match_live.CHANGED)

        assert view.refreshed == 0
        assert view._held_ids == {99}
        assert view._tidy_pill.text == '1 change held · Tidy up'

    async def test_an_empty_board_fills_straight_away(self):
        view = _view([], fetched=None)

        await view._on_remote_change(99, match_live.CHANGED)

        assert view.refreshed == 1

    async def test_a_change_to_a_listed_row_does_not_refetch_the_board(self):
        view = _view([SEEDED], fetched=SEEDED)

        await view._on_remote_change(1, match_live.CHANGED)

        assert view.refreshed == 0

    async def test_a_new_match_waits_for_a_tidy(self):
        view = _view([SEEDED], fetched=None)

        await view._on_remote_change(5, match_live.CREATED)

        assert view.refreshed == 0
        assert view.table.rows == [SEEDED]
        assert view._held_ids == {5}


class TestPredicateOnBoardRows:
    def test_the_room_board_shows_rolled_unfinished_matches(self):
        rows = (SEEDED, STARTED, FINISHED, UNSEEDED)
        assert [r for r in rows if is_room_seed_row(r)] == [SEEDED, STARTED]
