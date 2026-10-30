"""``update_row_by_id`` keeps every viewer-stamped field across a re-fetch.

Acknowledging a match on the web re-fetched its row, and the hand-kept carry list
did not include ``_can_reschedule``/``_reschedule_pending``, so the row lost its
Ask to change button until a reload (docs/reviews/player-journey-ux.md F4).
"""

import asyncio
from pathlib import Path

from theme.tables.row_state import carry_viewer_row_state

REPO = Path(__file__).resolve().parents[2]


def test_every_viewer_only_field_is_carried():
    old = {
        'id': 7, 'state': 'Scheduled',
        '_watching': True, '_stream_volunteer': False,
        '_some_future_field': 'kept',
    }
    fresh = {'id': 7, 'state': 'Scheduled'}
    carry_viewer_row_state(fresh, old)
    for key, value in old.items():
        assert fresh[key] == value, key


def test_fields_other_people_change_are_not_carried():
    old = {'id': 7, '_can_reschedule': False, '_reschedule_pending': True,
           '_hard_offered': True, '_hard_override': 'hard'}
    fresh = {'id': 7}
    carry_viewer_row_state(fresh, old)
    assert fresh == {'id': 7}


class _FakeTable:
    def __init__(self, rows):
        self.rows = rows

    def update(self):
        pass


async def test_staff_approving_a_move_brings_ask_to_change_back():
    """Approval publishes match_live 'changed'; the flash re-fetch used to carry
    "Change requested" across, so the moved match still read as waiting on staff."""
    from theme.tables.match import MatchTableView

    view = object.__new__(MatchTableView)
    view.table = _FakeTable([{
        'id': 7, 'state': 'Scheduled', 'scheduled_at': 'old',
        '_watching': True, '_can_reschedule': False, '_reschedule_pending': True,
    }])
    view.columns = [{'name': 'players'}, {'name': 'my_actions'}]
    view.row_filter = None
    view.show_accommodations = False
    view.on_set_stage = None
    view.on_rows_changed = None
    view._row_update_lock = asyncio.Lock()

    async def fetch_match(match_id):
        return {'id': match_id, 'state': 'Scheduled', 'scheduled_at': 'new'}

    async def reschedule_state():
        return {7}, set()

    async def hard_states(rows):
        return {}

    view.display_service = type('D', (), {'get_match_for_display': staticmethod(fetch_match)})()
    view._fetch_reschedule_state = reschedule_state
    view._fetch_hard_preset_states = hard_states

    await view.update_row_by_id(7)

    row = view.table.rows[0]
    assert row['scheduled_at'] == 'new'
    assert row['_can_reschedule'] is True
    assert row['_reschedule_pending'] is False
    assert row['_watching'] is True
    assert row['_hard_offered'] is False


def test_fresh_values_win_and_the_flash_is_not_replayed():
    old = {'id': 7, '_flash': True, '_watching': False, 'state': 'Scheduled'}
    fresh = {'id': 7, '_watching': True, 'state': 'Started'}
    carry_viewer_row_state(fresh, old)
    assert fresh['_watching'] is True
    assert fresh['state'] == 'Started'
    assert '_flash' not in fresh


def test_update_row_by_id_uses_the_generic_carry():
    source = (REPO / 'theme' / 'tables' / 'match.py').read_text()
    body = source[source.index('async def _update_row'):]
    body = body[:body.index('\n    def ', 1)]
    assert 'carry_viewer_row_state(match_data, self.table.rows[idx])' in body
