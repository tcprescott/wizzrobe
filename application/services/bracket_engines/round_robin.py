"""Round-robin engine — circle-method scheduling with optional groups.

Pure structural code: no ORM, no NiceGUI, no async. ``generate`` partitions the
seeded field into balanced, seed-fair groups (snake distribution) and schedules a
single round robin per group via the circle method — every within-group pair
meets exactly once. Round robin has no bracket progression, so no
``winner_to`` / ``loser_to`` pointers are emitted; standings
(:mod:`.standings`) decide outcomes (docs/features/brackets.md).
"""

from __future__ import annotations

from typing import List, Optional

from application.services.tournament_strategies import register_strategy

from .base import GeneratedMatch


@register_strategy('bracket_format', 'round_robin')
class RoundRobinEngine:
    """Generative engine producing round-robin match graphs, one per group."""

    def generate(self, num_entries: int, config: Optional[dict]) -> List[GeneratedMatch]:
        if num_entries < 2:
            raise ValueError("round robin needs at least 2 entries")

        config = config or {}
        group_count = int(config.get('group_count', 1))
        if group_count < 1:
            raise ValueError("group_count must be >= 1")
        if group_count > num_entries:
            raise ValueError("group_count cannot exceed num_entries")

        matches: List[GeneratedMatch] = []
        for group_number, seeds in enumerate(self._snake_groups(num_entries, group_count), start=1):
            matches.extend(self._schedule_group(seeds, group_number))
        return matches

    @staticmethod
    def _snake_groups(num_entries: int, group_count: int) -> List[List[int]]:
        """Partition seeds ``1..num_entries`` into ``group_count`` snake groups.

        Seed 1→group 1, 2→group 2, …, then the direction reverses each pass, so
        group sizes differ by at most one and seed strength is spread evenly.
        """
        groups: List[List[int]] = [[] for _ in range(group_count)]
        for i in range(num_entries):
            pass_index = i // group_count
            offset = i % group_count
            col = offset if pass_index % 2 == 0 else group_count - 1 - offset
            groups[col].append(i + 1)
        return groups

    @staticmethod
    def _schedule_group(seeds: List[int], group_number: int) -> List[GeneratedMatch]:
        """Single round robin over ``seeds`` via the circle method."""
        matches: List[GeneratedMatch] = []
        players: List[Optional[int]] = list(seeds)
        if len(players) % 2 == 1:
            players.append(None)

        m = len(players)
        rounds = m - 1
        arrangement = list(players)
        for r in range(1, rounds + 1):
            position = 0
            for i in range(m // 2):
                a = arrangement[i]
                b = arrangement[m - 1 - i]
                if a is None or b is None:
                    continue
                position += 1
                matches.append(
                    GeneratedMatch(
                        round=r,
                        position=position,
                        entry1_seed=a,
                        entry2_seed=b,
                        group_number=group_number,
                    )
                )
            arrangement = [arrangement[0], arrangement[-1], *arrangement[1:-1]]

        return matches
