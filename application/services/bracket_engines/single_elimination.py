"""Single-elimination engine — emits the whole winners-bracket graph up front.

Pure structural code: no ORM, no NiceGUI, no async. ``generate`` maps a seeded
field onto ``GeneratedMatch`` nodes wired by ``winner_to`` pointers, with byes
placed structurally so higher seeds meet lower ones (docs/features/brackets.md).
"""

from __future__ import annotations

from application.services.tournament_strategies import register_strategy

from .base import GeneratedMatch, Slot, next_power_of_two, standard_seeding


@register_strategy('bracket_format', 'single_elim')
class SingleEliminationEngine:
    """Generative engine producing a single-elimination match graph."""

    def generate(self, num_entries: int, config: dict) -> list[GeneratedMatch]:
        if num_entries < 2:
            raise ValueError("single elimination needs at least 2 entries")

        config = config or {}

        size = next_power_of_two(num_entries)
        rounds = size.bit_length() - 1  # log2(size)
        order = standard_seeding(size)

        matches: list[GeneratedMatch] = []

        for j in range(size // 2):
            position = j + 1
            seed_a = order[2 * j]
            seed_b = order[2 * j + 1]
            # A seed number past the real field is a structural bye.
            entry1 = seed_a if seed_a <= num_entries else None
            entry2 = seed_b if seed_b <= num_entries else None
            # The seeding guarantees at most one bye per first-round match.
            assert entry1 is not None or entry2 is not None
            is_bye = (entry1 is None) != (entry2 is None)
            matches.append(
                GeneratedMatch(
                    round=1,
                    position=position,
                    entry1_seed=entry1,
                    entry2_seed=entry2,
                    winner_to=self._winner_slot(1, position, rounds),
                    is_bye=is_bye,
                )
            )

        # Rounds 2..rounds: empty shells filled later by feeders/the service.
        for r in range(2, rounds + 1):
            for position in range(1, (size >> r) + 1):
                matches.append(
                    GeneratedMatch(
                        round=r,
                        position=position,
                        winner_to=self._winner_slot(r, position, rounds),
                        label='Final' if r == rounds else None,
                    )
                )

        return matches

    @staticmethod
    def _winner_slot(round_: int, position: int, rounds: int) -> Slot | None:
        if round_ >= rounds:
            return None
        return Slot(
            round=round_ + 1,
            position=(position + 1) // 2,
            slot=1 if position % 2 == 1 else 2,
        )
