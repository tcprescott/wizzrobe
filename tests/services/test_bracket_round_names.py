"""Round naming, lifted out of ``theme/brackets/layout.py`` into the service layer.

Pure — no DB, no NiceGUI. The lift is what lets a Discord DM say "Semifinals"
(U5 depends on it), so the first thing worth pinning is that the move changed
nothing: ``layout.round_label`` and ``round_names.round_label`` must be the same
function, not two that happen to agree today.
"""

import pytest

from application.services.bracket_engines.round_names import (
    RoundNode,
    detect_finals_ids,
    round_label,
    round_names,
)


def test_layout_reexports_the_lifted_function():
    """The card layer imports it from ``.layout``; that must still be *this* one."""
    from theme.brackets import layout

    assert layout.round_label is round_label


class TestRoundLabel:
    @pytest.mark.parametrize('round_number,expected', [
        (4, 'Final'),
        (3, 'Semifinals'),
        (2, 'Quarterfinals'),
        (1, 'Round 1'),
    ])
    def test_single_elim_counts_down_to_the_final(self, round_number, expected):
        assert round_label(round_number, max_winners_round=4) == expected

    @pytest.mark.parametrize('round_number,expected', [
        (4, 'Winners Finals'),
        (3, 'Winners Semifinals'),
        (2, 'Winners Round 2'),
    ])
    def test_double_elim_names_the_winners_side(self, round_number, expected):
        assert round_label(
            round_number, max_winners_round=4, max_losers_magnitude=6,
        ) == expected

    def test_losers_rounds(self):
        assert round_label(-2, max_losers_magnitude=6) == 'Losers Round 2'
        assert round_label(-6, max_losers_magnitude=6) == 'Losers Finals'

    def test_finals_are_named_explicitly(self):
        assert round_label(5, is_grand_final=True) == 'Grand Finals'
        assert round_label(6, is_reset=True) == 'Grand Finals (Reset)'

    def test_two_entrant_double_elim_has_no_losers_bracket(self):
        """``double_elim`` is an explicit signal for exactly this shape."""
        assert round_label(
            1, max_winners_round=1, double_elim=True,
        ) == 'Winners Finals'


class TestDetectFinals:
    def test_grand_final_is_the_positive_round_fed_by_a_negative_one(self):
        nodes = [
            RoundNode(id=1, round=1, winner_to_id=3, loser_to_id=2),
            RoundNode(id=2, round=-1, winner_to_id=3),
            RoundNode(id=3, round=2, winner_to_id=4, loser_to_id=4),
            RoundNode(id=4, round=3),
        ]
        assert detect_finals_ids(nodes) == (3, 4)

    def test_single_elim_has_neither(self):
        nodes = [
            RoundNode(id=1, round=1, winner_to_id=2),
            RoundNode(id=2, round=2),
        ]
        assert detect_finals_ids(nodes) == (None, None)

    def test_empty_graph(self):
        assert detect_finals_ids([]) == (None, None)


class TestRoundNames:
    def test_single_elim_map(self):
        nodes = [
            RoundNode(id=1, round=1, winner_to_id=3),
            RoundNode(id=2, round=1, winner_to_id=3),
            RoundNode(id=3, round=2),
        ]
        assert round_names(nodes) == {1: 'Semifinals', 2: 'Final'}

    def test_the_finals_rounds_are_excluded_from_the_winners_depth(self):
        """Otherwise a double elim's grand final would be read as the WB final and
        every winners round would be named one step too early."""
        nodes = [
            RoundNode(id=1, round=1, winner_to_id=2, loser_to_id=3),
            RoundNode(id=2, round=2, winner_to_id=4, loser_to_id=4),
            RoundNode(id=3, round=-1, winner_to_id=2),
            RoundNode(id=4, round=3),
        ]
        names = round_names(nodes, double_elim=True)
        assert names[1] == 'Winners Finals'
        assert names[2] == 'Grand Finals'
        assert names[3] == 'Grand Finals (Reset)'
        assert names[-1] == 'Losers Finals'

    def test_empty_graph(self):
        assert round_names([]) == {}

    def test_a_flat_stage_never_counts_down_to_a_final(self):
        """A round-robin group emits no progression pointers, so its deepest
        round is only the last one — naming it "Final" (and the two before it
        Semifinals/Quarterfinals) invents a knockout the stage does not have."""
        nodes = [
            RoundNode(id=1, round=1),
            RoundNode(id=2, round=2),
            RoundNode(id=3, round=3),
        ]
        assert round_names(nodes, elimination=False) == {
            1: 'Round 1', 2: 'Round 2', 3: 'Round 3',
        }
        assert round_names(nodes) == {
            1: 'Quarterfinals', 2: 'Semifinals', 3: 'Final',
        }
