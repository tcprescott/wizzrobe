"""Double-elimination engine — emits the whole winners+losers graph up front.

Pure structural code: no ORM, no NiceGUI, no async. ``generate`` maps a seeded
field onto ``GeneratedMatch`` nodes wired by ``winner_to`` / ``loser_to``
pointers: a standard winners bracket (positive rounds), a losers bracket
(negative rounds) that receives each winners round's losers, a grand final, and
a conditional grand-final reset (docs/features/brackets.md).

Losers-bracket routing follows the standard halving pattern (WB-R1 losers pair,
then alternating drop-receiving and consolidation rounds down to a single losers
final). Positional feeding is used; perfect WB-rematch avoidance is not attempted.

Structure ported in spirit from the MIT-licensed ``smwa/python-tournaments``
``double_elimination`` package; our ``GeneratedMatch`` contract is authoritative.
"""

from __future__ import annotations

from application.services.tournament_strategies import register_strategy

from .base import GeneratedMatch, Slot, next_power_of_two, standard_seeding


@register_strategy('bracket_format', 'double_elim')
class DoubleEliminationEngine:
    """Generative engine producing a double-elimination match graph."""

    def generate(self, num_entries: int, config: dict) -> list[GeneratedMatch]:
        if num_entries < 2:
            raise ValueError("double elimination needs at least 2 entries")

        config = config or {}

        size = next_power_of_two(num_entries)
        w = size.bit_length() - 1
        order = standard_seeding(size)

        gf_round = w + 1
        reset_round = w + 2
        lb_rounds = 2 * w - 2

        matches: list[GeneratedMatch] = []

        for r in range(1, w + 1):
            count = size >> r
            for position in range(1, count + 1):
                if r == 1:
                    seed_a = order[2 * (position - 1)]
                    seed_b = order[2 * (position - 1) + 1]
                    entry1 = seed_a if seed_a <= num_entries else None
                    entry2 = seed_b if seed_b <= num_entries else None
                    assert entry1 is not None or entry2 is not None
                    is_bye = (entry1 is None) != (entry2 is None)
                else:
                    entry1 = entry2 = None
                    is_bye = False
                matches.append(
                    GeneratedMatch(
                        round=r,
                        position=position,
                        entry1_seed=entry1,
                        entry2_seed=entry2,
                        winner_to=self._wb_winner_slot(r, position, w, gf_round),
                        loser_to=self._wb_loser_slot(r, position, w, lb_rounds, gf_round),
                        is_bye=is_bye,
                        label='Winners Final' if r == w else None,
                    )
                )

        for lb in range(1, lb_rounds + 1):
            count = self._lb_round_size(lb, size)
            for position in range(1, count + 1):
                matches.append(
                    GeneratedMatch(
                        round=-lb,
                        position=position,
                        winner_to=self._lb_winner_slot(lb, position, lb_rounds, gf_round),
                        loser_to=None,
                        label='Losers Final' if lb == lb_rounds else None,
                    )
                )

        reset_enabled = config.get('grand_final_reset', True)
        matches.append(
            GeneratedMatch(
                round=gf_round,
                position=1,
                winner_to=Slot(reset_round, 1, 1) if reset_enabled else None,
                loser_to=Slot(reset_round, 1, 2) if reset_enabled else None,
                label='Grand Final',
            )
        )
        if reset_enabled:
            matches.append(
                GeneratedMatch(
                    round=reset_round,
                    position=1,
                    is_reset=True,
                    label='Grand Final (reset)',
                )
            )

        return matches

    @staticmethod
    def _lb_round_size(lb: int, size: int) -> int:
        """Number of matches in losers-bracket round ``lb`` (1-based)."""
        return size >> ((lb + 1) // 2 + 1)

    @staticmethod
    def _wb_winner_slot(r: int, position: int, w: int, gf_round: int) -> Slot:
        if r < w:
            return Slot(
                round=r + 1,
                position=(position + 1) // 2,
                slot=1 if position % 2 == 1 else 2,
            )
        return Slot(round=gf_round, position=1, slot=1)

    @staticmethod
    def _wb_loser_slot(
        r: int, position: int, w: int, lb_rounds: int, gf_round: int
    ) -> Slot:
        if r == w:
            if w == 1:
                return Slot(round=gf_round, position=1, slot=2)
            return Slot(round=-lb_rounds, position=1, slot=2)
        if r == 1:
            return Slot(
                round=-1,
                position=(position + 1) // 2,
                slot=1 if position % 2 == 1 else 2,
            )
        return Slot(round=-(2 * r - 2), position=position, slot=2)

    @staticmethod
    def _lb_winner_slot(lb: int, position: int, lb_rounds: int, gf_round: int) -> Slot:
        if lb == lb_rounds:
            return Slot(round=gf_round, position=1, slot=2)
        if lb % 2 == 1:
            return Slot(round=-(lb + 1), position=position, slot=1)
        return Slot(
            round=-(lb + 1),
            position=(position + 1) // 2,
            slot=1 if position % 2 == 1 else 2,
        )
