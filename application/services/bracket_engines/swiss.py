"""Swiss pairing engine — one round at a time from live standings.

Pure structural code: no ORM, no NiceGUI, no async. This module is only a thin
*adapter* over the ``swisspair`` library (MIT), which does the actual work: it
builds the score-group weighted graph and runs the min-cost perfect matching
that yields no-rematch, minimal-score-difference pairings plus a single
lowest-standing bye. Here we translate ``PairingPlayer`` state into
``swisspair.Player`` inputs and map the returned matches back to ``ref`` tuples
(docs/features/brackets.md).

Determinism note: when several matchings share the minimum cost (e.g. round one
with everyone on zero points), swisspair breaks the tie with an internal RNG, so
repeated calls return different — all legal — pairings. The ``pair_round``
contract requires determinism, so we encode each player's ``points`` as
``scaled_points * M + (N - rank)``: the high-order term keeps the score-group
structure (a half point still survives via the ``* 2`` scale), while the
low-order rank term is a deterministic tiebreak that makes the min-cost solution
unique. ``M`` is chosen far larger than any accumulated rank offset, so a real
score-group difference always outweighs the tiebreak — swisspair still pairs
within score groups; it just resolves in-group ties by adjacent rank.
"""

from __future__ import annotations

from typing import List, Optional, Tuple

import swisspair

from application.services.tournament_strategies import register_strategy

from .base import PairingPlayer


@register_strategy('bracket_format', 'swiss')
class SwissEngine:
    """Pairing engine producing one Swiss round from current standings.

    With fewer than two active (non-dropped) players there is nothing to pair:
    zero active players yields ``[]``; a single active player yields a single
    bye ``(ref, None)`` when that player is bye-eligible (``can_bye`` and not
    already ``received_bye``), else ``[]``.
    """

    def pair_round(
        self, players: List[PairingPlayer], config: Optional[dict] = None
    ) -> List[Tuple[int, Optional[int]]]:
        config = config or {}

        active = [p for p in players if not p.dropped]

        if len(active) < 2:
            if len(active) == 1:
                p = active[0]
                if p.can_bye and not p.received_bye:
                    return [(p.ref, None)]
            return []

        active_refs = {p.ref for p in active}
        n = len(active)

        # Points scaled by 2 so half-point draws survive integer conversion.
        scaled = {p.ref: round(p.points * 2) for p in active}
        ordered = sorted(active, key=lambda p: (-scaled[p.ref], p.ref))

        # ``mult`` must dwarf any accumulated rank offset so real score gaps always dominate.
        mult = 8 * n * n

        sp_players = []
        for i, p in enumerate(ordered):
            rank = i + 1
            encoded = scaled[p.ref] * mult + (n - rank)
            sp_players.append(
                swisspair.Player(
                    id=str(p.ref),
                    points=encoded,
                    rank=rank,
                    can_get_bye=p.can_bye and not p.received_bye,
                    # Only reference opponents still in the field: swisspair
                    # rejects a no-rematch id that isn't among the players.
                    cannot_be_paired_against_ids={
                        str(o) for o in p.opponents if o in active_refs
                    },
                )
            )

        matches = swisspair.create_matches(sp_players)

        return [
            (int(m.p1.id), int(m.p2.id) if m.p2 is not None else None)
            for m in matches
        ]
