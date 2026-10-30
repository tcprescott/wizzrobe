"""Structural + simulation invariants for the double-elimination engine.

Every generated graph must validate, and playing it out under a variety of
deterministic winner-pick policies must always yield exactly one champion, every
other entrant eliminated with exactly two losses, no self-pairings, a reset
contested iff the losers-bracket side won the first grand final, and the right
total number of contested matches.
"""

from __future__ import annotations

from dataclasses import astuple

import pytest

from application.services.bracket_engines import get_bracket_engine

from ._bracket_sim import simulate_double_elim, validate_graph


def _pickers():
    """A deterministic family of ``winner_pick`` policies (no randomness source)."""
    policies = [
        ('higher_seed', lambda a, b: min(a, b)),
        ('lower_seed', lambda a, b: max(a, b)),
    ]
    # Pseudo-random-but-deterministic pickers derived purely from the seeds.
    for salt in (1, 7, 13, 29):
        def make(salt):
            def pick(a, b):
                h = (a * 1103515245 + b * 12345 + salt * 2654435761) & 0xFFFFFFFF
                h ^= h >> 16
                return a if (h & 1) == 0 else b
            return pick
        policies.append((f'mixed_{salt}', make(salt)))
    return policies


def test_rejects_fewer_than_two():
    engine = get_bracket_engine('double_elim')()
    for bad in (0, 1):
        with pytest.raises(ValueError):
            engine.generate(bad, {})


@pytest.mark.parametrize('n', range(2, 65))
def test_double_elim_invariants(n):
    engine = get_bracket_engine('double_elim')()
    matches = engine.generate(n, {})
    validate_graph(matches)

    for name, pick in _pickers():
        result = simulate_double_elim(matches, n, pick)

        champion = result['champion']
        assert champion is not None, f"no champion (n={n}, {name})"

        losses = result['losses']
        for seed in range(1, n + 1):
            if seed == champion:
                # Champion has 0 losses (won GF1) or 1 (lost GF1, won reset).
                assert losses.get(seed, 0) in (0, 1), (
                    f"champion {seed} has {losses.get(seed, 0)} losses "
                    f"(n={n}, {name})"
                )
            else:
                assert losses.get(seed, 0) == 2, (
                    f"seed {seed} has {losses.get(seed, 0)} losses, expected 2 "
                    f"(n={n}, {name})"
                )

        # The champion's loss count encodes whether the reset was needed.
        expected_reset = losses.get(champion, 0) == 1
        assert result['reset_played'] == expected_reset, (
            f"reset_played={result['reset_played']} but champion losses="
            f"{losses.get(champion, 0)} (n={n}, {name})"
        )

        expected_played = 2 * n - 1 if result['reset_played'] else 2 * n - 2
        assert result['played'] == expected_played, (
            f"played={result['played']}, expected {expected_played} "
            f"(n={n}, {name})"
        )


@pytest.mark.parametrize('n', range(2, 65))
def test_determinism(n):
    engine = get_bracket_engine('double_elim')()
    a = engine.generate(n, {})
    b = engine.generate(n, {})
    assert [astuple(m) for m in a] == [astuple(m) for m in b]
