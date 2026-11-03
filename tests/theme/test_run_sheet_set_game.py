"""Every run sheet, desktop and phone, says which game of a set a row is."""

import pytest

from theme.tables.match_player_run_sheet import _BODY_SLOT, _ITEM_SLOT
from theme.tables.match_run_sheet import _body_slot, _item_slot

SLOTS = {
    'admin desktop': _body_slot(False),
    'admin phone': _item_slot(False),
    'admin phone with approve': _item_slot(False, approve='true'),
    'proctor desktop': _body_slot(True, can_run=True),
    'proctor phone': _item_slot(True, can_run=True),
    'event desktop': _BODY_SLOT,
    'event phone': _ITEM_SLOT,
}


@pytest.mark.parametrize('name', SLOTS)
def test_set_game_and_bracket_game_shown(name):
    slot = SLOTS[name]
    assert '{{ props.row.set_label }}</span>' in slot
    assert 'of {{ props.row.bracket.best_of }}' in slot
