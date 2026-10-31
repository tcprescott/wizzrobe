"""Recording a winner takes two taps, not one.

Every other lifecycle step asks before it acts — check-in, start and confirm all
open a confirmation. Recording the winner was the exception: on the two-player
path (the common one, and the one that happens under time pressure in a room)
tapping a name committed immediately, settling the match and advancing the
bracket, and a proctor has no way to undo it — the pencil is admin-only.

Rendering needs a live NiceGUI client, so these lock the state machine and the
wiring that ``open()`` builds on.
"""

import inspect

from theme.dialog.match_result_dialog import MatchResultDialog


def dialog(mode='record'):
    return MatchResultDialog(match=None, mode=mode)


class TestPendingWinner:
    def test_starts_with_nothing_picked(self):
        assert dialog()._pending_winner_id is None

    def test_choosing_records_the_pick_without_submitting(self):
        d = dialog()
        d._choose(42)
        assert d._pending_winner_id == 42

    def test_choosing_none_clears_the_pick(self):
        d = dialog()
        d._choose(42)
        d._choose(None)
        assert d._pending_winner_id is None

    def test_choosing_refreshes_the_picker(self):
        calls = []
        d = dialog()
        d._refresh_choice = lambda: calls.append(1)
        d._choose(42)
        assert calls == [1]

    def test_choosing_survives_a_missing_refresh_hook(self):
        """The hook is only bound on the two-player path."""
        d = dialog()
        d._choose(42)
        assert d._pending_winner_id == 42


class TestWiring:
    def test_a_player_button_picks_rather_than_records(self):
        source = inspect.getsource(MatchResultDialog._render_player_buttons)
        assert 'self._choose' in source
        assert '_submit_winner' not in source

    def test_only_the_second_step_records(self):
        source = inspect.getsource(MatchResultDialog._render_confirm_step)
        assert '_submit_winner' in source

    def test_the_second_step_offers_a_way_back(self):
        source = inspect.getsource(MatchResultDialog._render_confirm_step)
        assert "'Back'" in source
        assert '_choose(None)' in source


class TestConfirmStepCopy:
    def test_record_mode_says_what_happens_next(self):
        source = inspect.getsource(MatchResultDialog._render_confirm_step)
        assert 'hands the match to an admin' in source

    def test_edit_mode_says_it_replaces_the_recorded_winner(self):
        source = inspect.getsource(MatchResultDialog._render_confirm_step)
        assert 'replaces the winner already on the board' in source


def test_the_station_stays_on_the_player_button():
    """The proctor checks the name against the room, so the seat must be on it."""
    source = inspect.getsource(MatchResultDialog._player_label)
    assert 'Station' in source
    assert 'assigned_station' in source
