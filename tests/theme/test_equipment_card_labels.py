"""The equipment register's actions name themselves, and Delete stays out of reach.

The register's card used to end in four icon-only buttons explained only by a
`q-tooltip`, which never opens on a phone. Labelling all four fixed that but
crowded the card (Delete wrapped onto a line of its own, and the solid Check in
button touched the line above). The card and the desktop row now share one
shape: the asset's state action as a labelled button, and a menu holding Open,
Edit and, under a divider, Delete.
"""

import re

from pages.admin_tabs.admin_equipment import _ACTIONS_CELL, _GRID_CARD, _ROW_ACTIONS


def _labels(template: str) -> set:
    return set(re.findall(r'(?<![-\w])label="([^"]+)"', template))


def _menu_items(template: str) -> list:
    return re.findall(r'<q-item\b[^>]*>.*?</q-item>', template, re.S)


class TestRegisterActions:
    def test_card_and_row_share_one_shape(self):
        assert _ROW_ACTIONS in _GRID_CARD
        assert _ROW_ACTIONS in _ACTIONS_CELL

    def test_state_actions_are_labelled_buttons(self):
        assert _labels(_ROW_ACTIONS) == {'Check out', 'Check in'}

    def test_everything_else_is_a_named_menu_entry(self):
        items = _menu_items(_ROW_ACTIONS)
        texts = [re.findall(r'<q-item-section>([^<]+)</q-item-section>', i)[0] for i in items]
        assert texts == ['Open asset page', 'Edit', 'Delete']

    def test_menu_trigger_has_a_name(self):
        trigger = re.search(r'<q-btn\b[^>]*icon="more_vert"[^>]*>', _ROW_ACTIONS).group(0)
        assert 'aria-label' in trigger

    def test_delete_is_last_and_set_apart(self):
        items = _menu_items(_ROW_ACTIONS)
        assert "$emit('remove'" in items[-1]
        assert 'negative' in items[-1]
        before_delete = _ROW_ACTIONS[:_ROW_ACTIONS.index(items[-1])]
        assert before_delete.rstrip().endswith('<q-separator />')

