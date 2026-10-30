"""Reusable bracket-graph simulation harness (NOT a test module).

Engine-agnostic helpers other engine units (double-elim, etc.) reuse to validate
the structural output of a ``generate()`` call and to play a match graph to a
champion. Leading underscore keeps pytest from collecting it.
"""

from __future__ import annotations

from typing import Callable, Dict, Optional, Tuple

from application.services.bracket_engines import GeneratedMatch


def build_index(matches) -> Dict[Tuple[int, int], GeneratedMatch]:
    """Index a match list by ``(round, position)``."""
    return {(m.round, m.position): m for m in matches}


def validate_graph(matches) -> None:
    """Assert the graph is internally consistent.

    Unique ``(round, position)`` keys; every ``winner_to``/``loser_to`` points at
    an existing match with ``slot`` in {1, 2}; no match places the same seed in
    both slots.
    """
    keys = [(m.round, m.position) for m in matches]
    assert len(keys) == len(set(keys)), "duplicate (round, position)"

    index = build_index(matches)
    for m in matches:
        for dest in (m.winner_to, m.loser_to):
            if dest is None:
                continue
            assert dest.slot in (1, 2), f"bad slot {dest.slot}"
            assert (dest.round, dest.position) in index, (
                f"dangling pointer to ({dest.round}, {dest.position})"
            )
        if m.entry1_seed is not None and m.entry2_seed is not None:
            assert m.entry1_seed != m.entry2_seed, "same seed in both slots"


def simulate_single_elim(
    matches,
    num_entries: int,
    winner_pick: Callable[[int, int], int],
) -> dict:
    """Play a single-elimination graph to completion.

    ``winner_pick(seed_a, seed_b) -> winning_seed`` decides each contested match.
    Byes and initial seeds come from the graph's round-1 placements; ``is_bye``
    matches auto-resolve to the present seed. Returns ``champion``, ``losses``
    (seed -> loss count), ``played`` (contested, non-bye matches) and
    ``bye_count``.
    """
    index = build_index(matches)

    # slots[(round, position)] = [entry1_seed_or_None, entry2_seed_or_None]
    slots: Dict[Tuple[int, int], list] = {
        key: [m.entry1_seed, m.entry2_seed] for key, m in index.items()
    }
    resolved: set = set()
    losses: Dict[int, int] = {}
    played = 0
    bye_count = 0
    champion: Optional[int] = None

    def place(dest, seed: int) -> None:
        slots[(dest.round, dest.position)][dest.slot - 1] = seed

    progressed = True
    while progressed:
        progressed = False
        for key, m in index.items():
            if key in resolved:
                continue
            a, b = slots[key]
            if m.is_bye:
                present = a if a is not None else b
                if present is None:
                    continue
                winner = present
                bye_count += 1
            else:
                if a is None or b is None:
                    continue
                winner = winner_pick(a, b)
                loser = b if winner == a else a
                losses[loser] = losses.get(loser, 0) + 1
                played += 1
            resolved.add(key)
            progressed = True
            if m.winner_to is not None:
                place(m.winner_to, winner)
            else:
                champion = winner

    return {
        'champion': champion,
        'losses': losses,
        'played': played,
        'bye_count': bye_count,
    }


# Sentinels for the double-elim dataflow: a slot is either awaiting a feeder
# (_UNRESOLVED), permanently empty (_EMPTY — a phantom/bye propagated in), or a
# concrete seed.
_UNRESOLVED = object()
_EMPTY = object()


def simulate_double_elim(
    matches,
    num_entries: int,
    winner_pick: Callable[[int, int], int],
) -> dict:
    """Play a double-elimination graph to completion via proper dataflow.

    A match resolves once both its slots are settled — each slot is settled when
    it holds a real seed or is permanently empty (its feeder produced a
    phantom/bye, or it has no feeder and no seed). Real-vs-real matches are
    contested by ``winner_pick(seed_a, seed_b) -> winning_seed``; a lone real
    entrant walks over. Winners flow via ``winner_to``; losers via ``loser_to``.

    The grand-final reset (``is_reset``) is contested **only** when the grand
    final's winner entered it through slot 2 (the losers-bracket side). Returns
    ``champion``, ``losses`` (seed -> loss count), ``played`` (contested,
    non-bye matches, including finals) and ``reset_played`` (bool).
    """
    index = build_index(matches)

    reset_key = next((k for k, m in index.items() if m.is_reset), None)
    gf_key = None
    if reset_key is not None:
        for k, m in index.items():
            if m.winner_to is not None and (
                m.winner_to.round,
                m.winner_to.position,
            ) == reset_key:
                gf_key = k
                break

    fed: set = set()
    for m in matches:
        for dest in (m.winner_to, m.loser_to):
            if dest is not None:
                fed.add((dest.round, dest.position, dest.slot))

    # slots[(round, position)] = [slot1_value, slot2_value]
    slots: Dict[Tuple[int, int], list] = {}
    for key, m in index.items():
        row = []
        for s, seed in ((1, m.entry1_seed), (2, m.entry2_seed)):
            if (key[0], key[1], s) in fed:
                row.append(_UNRESOLVED)
            else:
                row.append(seed if seed is not None else _EMPTY)
        slots[key] = row

    resolved: set = set()
    losses: Dict[int, int] = {}
    played = 0
    reset_played = False
    champion: Optional[int] = None

    def place(dest, value) -> None:
        slots[(dest.round, dest.position)][dest.slot - 1] = (
            _EMPTY if value is None else value
        )

    def contest(v1: int, v2: int) -> int:
        assert v1 != v2, "match pairs a seed against itself"
        winner = winner_pick(v1, v2)
        assert winner in (v1, v2), "winner_pick returned a non-participant"
        return winner

    progressed = True
    while progressed:
        progressed = False
        for key, m in index.items():
            if key in resolved or key == reset_key:
                continue
            a, b = slots[key]
            if a is _UNRESOLVED or b is _UNRESOLVED:
                continue

            present = [(s, v) for s, v in ((1, a), (2, b)) if v is not _EMPTY]
            resolved.add(key)
            progressed = True

            if len(present) == 0:
                winner = None
                winner_slot = None
                loser = None
            elif len(present) == 1:
                winner_slot, winner = present[0]
                loser = None
            else:
                (s1, v1), (s2, v2) = present
                winner = contest(v1, v2)
                if winner == v1:
                    winner_slot, loser = s1, v2
                else:
                    winner_slot, loser = s2, v1
                losses[loser] = losses.get(loser, 0) + 1
                played += 1

            if key == gf_key:
                if winner_slot == 2:
                    # Losers-bracket side won GF1 -> the reset is contested.
                    slots[reset_key][0] = _EMPTY if winner is None else winner
                    slots[reset_key][1] = _EMPTY if loser is None else loser
                else:
                    champion = winner
                continue

            if m.winner_to is not None:
                place(m.winner_to, winner)
            else:
                champion = winner
            if m.loser_to is not None:
                place(m.loser_to, loser)

    if reset_key is not None:
        a, b = slots[reset_key]
        if a is not _UNRESOLVED and b is not _UNRESOLVED and (
            a is not _EMPTY and b is not _EMPTY
        ):
            winner = contest(a, b)
            loser = b if winner == a else a
            losses[loser] = losses.get(loser, 0) + 1
            played += 1
            reset_played = True
            champion = winner

    return {
        'champion': champion,
        'losses': losses,
        'played': played,
        'reset_played': reset_played,
    }
