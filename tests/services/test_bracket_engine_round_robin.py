"""Structural invariants for the round-robin engine across field sizes/groups."""

from __future__ import annotations

from dataclasses import astuple
from itertools import combinations

import pytest

from application.services.bracket_engines import get_bracket_engine


def _engine():
    return get_bracket_engine('round_robin')()


def _groups(matches):
    """Map group_number -> set of frozenset pairs scheduled in that group."""
    out: dict[int, list] = {}
    for m in matches:
        out.setdefault(m.group_number, []).append(
            frozenset((m.entry1_seed, m.entry2_seed))
        )
    return out


def test_rejects_fewer_than_two():
    engine = _engine()
    for n in (0, 1):
        with pytest.raises(ValueError):
            engine.generate(n, {})


def test_rejects_bad_group_count():
    engine = _engine()
    with pytest.raises(ValueError):
        engine.generate(8, {'group_count': 0})
    with pytest.raises(ValueError):
        engine.generate(4, {'group_count': 5})


def test_config_none_treated_as_empty():
    engine = _engine()
    matches = engine.generate(4, None)
    assert {m.group_number for m in matches} == {1}


@pytest.mark.parametrize('n', range(2, 41))
@pytest.mark.parametrize('group_count', (1, 2, 4))
def test_round_robin_invariants(n, group_count):
    if group_count > n:
        pytest.skip("group_count cannot exceed num_entries")
    engine = _engine()
    matches = engine.generate(n, {'group_count': group_count})

    expected_groups: list[list[int]] = [[] for _ in range(group_count)]
    for i in range(n):
        pass_index = i // group_count
        offset = i % group_count
        col = offset if pass_index % 2 == 0 else group_count - 1 - offset
        expected_groups[col].append(i + 1)

    sizes = [len(g) for g in expected_groups]
    assert max(sizes) - min(sizes) <= 1
    assert sorted(s for g in expected_groups for s in g) == list(range(1, n + 1))

    groups = _groups(matches)
    assert set(groups) == {
        gn for gn in range(1, group_count + 1) if len(expected_groups[gn - 1]) >= 2
    }

    seen_positions = {}
    for m in matches:
        assert m.entry1_seed != m.entry2_seed
        assert m.winner_to is None and m.loser_to is None
        assert m.entry1_seed is not None and m.entry2_seed is not None
        key = (m.group_number, m.round)
        seen_positions.setdefault(key, set())
        assert m.position not in seen_positions[key]
        seen_positions[key].add(m.position)

    for group_number, pairs in groups.items():
        members = expected_groups[group_number - 1]
        member_set = set(members)
        for pair in pairs:
            assert pair <= member_set
        expected_pairs = {frozenset(p) for p in combinations(members, 2)}
        assert len(pairs) == len(expected_pairs)
        assert set(pairs) == expected_pairs


def test_known_four_in_one_group():
    engine = _engine()
    matches = engine.generate(4, {'group_count': 1})
    assert max(m.round for m in matches) == 3
    assert len(matches) == 6
    pairs = {frozenset((m.entry1_seed, m.entry2_seed)) for m in matches}
    assert pairs == {frozenset(p) for p in combinations(range(1, 5), 2)}
    for r in (1, 2, 3):
        assert len([m for m in matches if m.round == r]) == 2


def test_odd_group_has_no_phantom_matches():
    engine = _engine()
    matches = engine.generate(5, {'group_count': 1})
    assert len(matches) == 10
    assert max(m.round for m in matches) == 5
    for m in matches:
        assert m.entry1_seed in range(1, 6)
        assert m.entry2_seed in range(1, 6)


@pytest.mark.parametrize('n', range(2, 41))
@pytest.mark.parametrize('group_count', (1, 2, 4))
def test_determinism(n, group_count):
    if group_count > n:
        pytest.skip("group_count cannot exceed num_entries")
    engine = _engine()
    a = engine.generate(n, {'group_count': group_count})
    b = engine.generate(n, {'group_count': group_count})
    assert [astuple(m) for m in a] == [astuple(m) for m in b]
