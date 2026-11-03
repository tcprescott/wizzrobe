"""The edit dialog must describe the match it is editing.

Three of its statements were wrong or missing, and all three were found by
opening the dialog on one real match — Finished, flagged for review, with a
winner recorded and two players. It showed none of those four facts, offered
five unlabelled arming buttons, and said "No players assigned."

These are pure-string tests against the helpers the dialog renders from; the
NiceGUI layer around them has no coverage by design (see current-state.md), so
the copy has to be testable without it.
"""

import inspect

import pytest

from theme.dialog.match_dialog_base import BaseMatchDialog


class TestRewindMenu:
    """Rewinding is one step picker behind the ⋮, not five arming buttons."""

    def test_the_footer_menu_offers_rewind_and_clear_seed(self):
        src = inspect.getsource(BaseMatchDialog._render_submit_footer)
        assert "'Rewind…'" in src
        assert "'Clear seed…'" in src
        assert "'Call off match…'" in src
        assert src.index("'Clear seed…'") < src.index("'Delete match…'")

    def test_clearing_the_seed_confirms_first(self):
        src = inspect.getsource(BaseMatchDialog._confirm_clear_seed)
        assert 'ConfirmationDialog' in src


class TestStateSummary:
    """RC2: the dialog read none of the match's own state."""

    def test_it_reads_the_three_facts_that_explain_the_match(self):
        src = inspect.getsource(BaseMatchDialog._render_state_summary)
        for fact in ('resolve(match=self.match)', 'finish_rank', 'needs_review'):
            assert fact in src, f'the state summary never reads {fact}'

    def test_the_dispute_flag_is_text_not_a_tooltip(self):
        """The board hides its explanation behind a hover; repeating that here
        would leave it unreadable on every touch screen."""
        src = inspect.getsource(BaseMatchDialog._render_state_summary)
        flag = src[src.index("'Flagged by the proctor"):]
        assert 'tooltip' not in flag.lower()

    @pytest.mark.parametrize('tone', [
        'neutral', 'info', 'imminent', 'live', 'done', 'settled', 'cancelled', 'attention',
    ])
    def test_every_status_tone_maps_to_a_chip(self, tone):
        """A missing tone would silently fall back to neutral, so a Live match
        and a Cancelled one would render identically."""
        from application.services.match.match_status import STATUS_TONES
        from theme.dialog.match_dialog_base import _TONE_CHIP

        assert tone in _TONE_CHIP
        assert set(STATUS_TONES.values()) <= set(_TONE_CHIP)


class TestPlayerDialogTenantBinding:
    """Sentry SGLMAN-16: the player's tournament/opponent re-run needs the tenant."""

    def test_selection_reruns_carry_the_render_context(self):
        from theme.dialog.match_dialog import UserMatchDialog

        src = inspect.getsource(UserMatchDialog.open)
        assert 'background_tasks.create(update_selection_options())' not in src
        assert src.count('scoped_background(render_context, update_selection_options())') == 2
