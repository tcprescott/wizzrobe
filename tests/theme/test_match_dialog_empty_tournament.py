"""The Tournament select when a community has no tournaments.

Measured on a new community: the required select opened no menu at all (zero
options, no "none yet" row) and Create Match answered "Please fill required
field(s): Tournament" — a control that cannot succeed and does not say why.

Rendering the select needs a live NiceGUI client, so this locks the two pieces
that are inspectable without one: that the helper takes an audience-specific
hint at all, and that both call sites pass a distinct one.
"""

import ast
import inspect
from pathlib import Path

from theme.dialog import match_dialog
from theme.dialog.match_dialog_base import BaseMatchDialog

REPO = Path(__file__).resolve().parents[2]


class TestTheHelperTakesAHint:
    def test_render_tournament_select_requires_an_empty_hint(self):
        params = inspect.signature(BaseMatchDialog._render_tournament_select).parameters
        assert 'empty_hint' in params

    def test_it_disables_the_select_when_there_are_no_tournaments(self):
        source = inspect.getsource(BaseMatchDialog._render_tournament_select)
        assert 'if not tournaments:' in source
        assert 'select.disable()' in source
        assert 'empty_hint' in source.split('if not tournaments:')[1]


class TestBothDialogsGetAudienceSpecificCopy:
    def test_the_two_hints_differ(self):
        admin = match_dialog._ADMIN_NO_TOURNAMENTS_HINT
        player = match_dialog._PLAYER_NO_TOURNAMENTS_HINT
        assert admin != player

    def test_the_admin_hint_names_where_to_make_one(self):
        assert 'Tournaments tab' in match_dialog._ADMIN_NO_TOURNAMENTS_HINT

    def test_the_player_hint_does_not_send_them_to_an_admin_tab(self):
        assert 'tab' not in match_dialog._PLAYER_NO_TOURNAMENTS_HINT.lower()

    def test_neither_hint_carries_a_double_quote(self):
        for hint in (match_dialog._ADMIN_NO_TOURNAMENTS_HINT,
                     match_dialog._PLAYER_NO_TOURNAMENTS_HINT):
            assert '"' not in hint

    def test_every_call_site_passes_a_hint(self):
        source = (REPO / 'theme/dialog/match_dialog.py').read_text()
        tree = ast.parse(source)
        calls = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == '_render_tournament_select'
        ]
        assert len(calls) == 2, 'expected the admin and player dialogs'
        for call in calls:
            assert len(call.args) == 3, ast.dump(call)
