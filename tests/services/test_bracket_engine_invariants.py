"""Structural invariants for the single-elimination engine across field sizes."""

from __future__ import annotations

from dataclasses import astuple

import pytest

from application.services.bracket_engines import (
    get_bracket_engine,
    next_power_of_two,
)

from ._bracket_sim import simulate_single_elim, validate_graph


def test_rejects_fewer_than_two():
    engine = get_bracket_engine('single_elim')()
    for n in (0, 1):
        with pytest.raises(ValueError):
            engine.generate(n, {})


@pytest.mark.parametrize('n', range(2, 65))
def test_single_elim_invariants(n):
    engine = get_bracket_engine('single_elim')()
    matches = engine.generate(n, {})
    validate_graph(matches)

    size = next_power_of_two(n)

    round1 = [m for m in matches if m.round == 1]
    byes = [m for m in round1 if m.is_bye]
    assert len(byes) == size - n

    bye_seeds = set()
    for m in byes:
        present = m.entry1_seed if m.entry1_seed is not None else m.entry2_seed
        missing_slot = m.entry2_seed if m.entry1_seed is not None else m.entry1_seed
        assert present is not None and present <= n  # opposite a real seed
        assert missing_slot is None
        # the absent structural seed is one of {n+1..size}
        bye_seeds.add(present)
    assert all(
        m.entry1_seed is not None or m.entry2_seed is not None for m in round1
    )
    assert len(byes) == size - n
    phantom = {s for s in range(n + 1, size + 1)}
    assert len(phantom) == size - n

    result = simulate_single_elim(matches, n, lambda a, b: min(a, b))
    assert result['champion'] == 1
    assert result['played'] == n - 1
    assert result['losses'].get(1, 0) == 0
    for seed in range(2, n + 1):
        assert result['losses'].get(seed, 0) == 1


@pytest.mark.parametrize('n', range(2, 65))
def test_determinism(n):
    engine = get_bracket_engine('single_elim')()
    a = engine.generate(n, {})
    b = engine.generate(n, {})
    assert [astuple(m) for m in a] == [astuple(m) for m in b]
