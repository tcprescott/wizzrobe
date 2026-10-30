"""Unit tests for the pure bracket layout walker (theme/brackets/layout.py).

No DB / NiceGUI: the walker is pure geometry, so these assert the positioning
invariants that make a bracket read correctly — leaves in top-to-bottom order,
every parent centered on its children, columns tracking rounds, and elbow
connectors joining the right cards — across single-elim, the irregular
double-elim losers bracket, byes, and the finals.
"""

import itertools

from theme.brackets.layout import (
    CARD_HEIGHT,
    MatchNode,
    assign_match_numbers,
    layout_section,
    round_label,
    slot_sources,
)


def _se4():
    # 4-entrant single elimination: two round-1 matches feed the final.
    return [
        MatchNode(id=1, round=1, position=0, winner_to_id=3),
        MatchNode(id=2, round=1, position=1, winner_to_id=3),
        MatchNode(id=3, round=2, position=0, winner_to_id=None),
    ]


def _se8():
    return [
        MatchNode(id=1, round=1, position=0, winner_to_id=5),
        MatchNode(id=2, round=1, position=1, winner_to_id=5),
        MatchNode(id=3, round=1, position=2, winner_to_id=6),
        MatchNode(id=4, round=1, position=3, winner_to_id=6),
        MatchNode(id=5, round=2, position=0, winner_to_id=7),
        MatchNode(id=6, round=2, position=1, winner_to_id=7),
        MatchNode(id=7, round=3, position=0, winner_to_id=None),
    ]


class TestElimination:
    def test_empty(self):
        layout = layout_section([])
        assert layout.placements == {}
        assert layout.columns == []

    def test_single_match(self):
        layout = layout_section([MatchNode(id=1, round=1, position=0)])
        p = layout.placements[1]
        assert p.col == 0 and p.slot == 0.0

    def test_se4_columns_and_centering(self):
        layout = layout_section(_se4())
        assert layout.columns == [1, 2]
        assert layout.placements[1].col == 0
        assert layout.placements[2].col == 0
        assert layout.placements[3].col == 1
        # Leaves stacked; final centered between them.
        assert layout.placements[1].slot == 0.0
        assert layout.placements[2].slot == 1.0
        assert layout.placements[3].slot == 0.5
        mean_y = (layout.placements[1].center_y + layout.placements[2].center_y) / 2
        assert layout.placements[3].center_y == mean_y

    def test_se8_slots(self):
        layout = layout_section(_se8())
        assert layout.columns == [1, 2, 3]
        slots = {mid: p.slot for mid, p in layout.placements.items()}
        assert (slots[1], slots[2], slots[3], slots[4]) == (0.0, 1.0, 2.0, 3.0)
        assert slots[5] == 0.5
        assert slots[6] == 2.5
        assert slots[7] == 1.5

    def test_no_column_overlap(self):
        layout = layout_section(_se8())
        by_col = {}
        for p in layout.placements.values():
            by_col.setdefault(p.col, []).append(p)
        for col_placements in by_col.values():
            ordered = sorted(col_placements, key=lambda p: p.top)
            for a, b in itertools.pairwise(ordered):
                assert a.top + CARD_HEIGHT <= b.top + 1e-9

    def test_connectors_form_elbow(self):
        layout = layout_section(_se4())
        # 2 child stubs + 1 spine + 1 parent stub.
        assert len(layout.connectors) == 4
        verticals = [s for s in layout.connectors if s.orientation == 'v']
        assert len(verticals) == 1
        spine = verticals[0]
        c1 = layout.placements[1].center_y
        c2 = layout.placements[2].center_y
        assert spine.y == min(c1, c2)
        assert abs(spine.length - abs(c2 - c1)) < 1e-9


class TestLosersBracket:
    def test_minor_round_single_child_and_major_round(self):
        # Two round -1 matches → a major round -2 (2 children) → a minor round -3
        # (single in-section child; the other slot arrives cross-section from WB).
        nodes = [
            MatchNode(id=10, round=-1, position=0, winner_to_id=12),
            MatchNode(id=11, round=-1, position=1, winner_to_id=12),
            MatchNode(id=12, round=-2, position=0, winner_to_id=13),
            MatchNode(id=13, round=-3, position=0, winner_to_id=None),
        ]
        layout = layout_section(nodes)
        assert layout.columns == [-1, -2, -3]
        assert layout.placements[12].slot == 0.5
        # Minor round (single feeder) sits at that feeder's height.
        assert layout.placements[13].slot == layout.placements[12].slot
        assert layout.placements[13].center_y == layout.placements[12].center_y


class TestMatchNumbers:
    def test_winners_before_losers(self):
        nodes = [
            MatchNode(id=1, round=1, position=0),
            MatchNode(id=2, round=2, position=0),
            MatchNode(id=3, round=-1, position=0),
            MatchNode(id=4, round=-1, position=1),
        ]
        nums = assign_match_numbers(nodes)
        assert nums[1] == 1
        assert nums[2] == 2
        assert nums[3] == 3
        assert nums[4] == 4


class TestSlotSources:
    def test_inversion(self):
        winner_links = [(1, 3, 1), (2, 3, 2)]
        loser_links = [(1, 9, 1)]
        src = slot_sources(winner_links, loser_links)
        assert src[(3, 1)].kind == 'winner' and src[(3, 1)].source_match_id == 1
        assert src[(3, 2)].kind == 'winner' and src[(3, 2)].source_match_id == 2
        assert src[(9, 1)].kind == 'loser' and src[(9, 1)].source_match_id == 1


class TestRoundLabels:
    def test_single_elim_names(self):
        assert round_label(3, max_winners_round=3) == 'Final'
        assert round_label(2, max_winners_round=3) == 'Semifinals'
        assert round_label(1, max_winners_round=3) == 'Quarterfinals'
        assert round_label(1, max_winners_round=4) == 'Round 1'

    def test_double_elim_names(self):
        assert round_label(3, max_winners_round=3, max_losers_magnitude=3) == 'Winners Finals'
        assert round_label(1, max_winners_round=3, max_losers_magnitude=3) == 'Winners Round 1'
        assert round_label(-3, max_losers_magnitude=3) == 'Losers Finals'
        assert round_label(-1, max_losers_magnitude=3) == 'Losers Round 1'
        assert round_label(4, is_grand_final=True) == 'Grand Finals'
        assert round_label(5, is_reset=True) == 'Grand Finals (Reset)'

    def test_two_entrant_double_elim_signal(self):
        # A 2-entrant double elim has NO losers bracket (max_losers_magnitude is
        # None), so the explicit double_elim flag must still name the winners side
        # as a double elim — not "Quarterfinals"/"Final".
        assert round_label(1, max_winners_round=1, double_elim=True) == 'Winners Finals'
        # Without the flag (and no losers magnitude) it would mis-name as a final.
        assert round_label(1, max_winners_round=1) == 'Final'
        # double_elim must suppress the single-elim "Quarterfinals" branch.
        assert round_label(1, max_winners_round=3, double_elim=True) == 'Winners Round 1'
