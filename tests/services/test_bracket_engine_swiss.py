"""Multi-round Swiss simulation exercising the swiss pairing engine — no DB.

Player state is tracked in plain dicts (points/opponents/received_bye); each
round we pair via the engine, resolve results deterministically, and update
state, then assert the Swiss invariants across the whole run.
"""

from __future__ import annotations

import math

import pytest

from application.services.bracket_engines import get_bracket_engine
from application.services.bracket_engines.base import PairingPlayer


def _engine():
    return get_bracket_engine('swiss')()


def _build_players(state, drops=frozenset()):
    """PairingPlayer list from the mutable state dict, honouring drops."""
    return [
        PairingPlayer(
            ref=ref,
            points=s['points'],
            opponents=frozenset(s['opponents']),
            received_bye=s['received_bye'],
            can_bye=True,
            dropped=ref in drops,
        )
        for ref, s in state.items()
    ]


def _apply_results(state, pairings):
    """Deterministic results: higher current points wins, ties by lower ref.

    Byes award a full point and set ``received_bye``.
    """
    for a, b in pairings:
        if b is None:
            state[a]['points'] += 1.0
            state[a]['received_bye'] = True
            continue
        state[a]['opponents'].add(b)
        state[b]['opponents'].add(a)
        # Winner: higher points, tie broken by lower ref.
        if (state[a]['points'], -a) >= (state[b]['points'], -b):
            winner = a
        else:
            winner = b
        state[winner]['points'] += 1.0


def _run(n, rounds=None, drops_by_round=None):
    """Run a full Swiss sim over ``n`` players; return the per-round pairings."""
    engine = _engine()
    state = {
        ref: {'points': 0.0, 'opponents': set(), 'received_bye': False}
        for ref in range(1, n + 1)
    }
    if rounds is None:
        rounds = math.ceil(math.log2(n))
    drops_by_round = drops_by_round or {}

    all_rounds = []
    dropped = set()
    for r in range(rounds):
        dropped |= drops_by_round.get(r, set())
        players = _build_players(state, drops=dropped)
        pairings = engine.pair_round(players, {})
        all_rounds.append((pairings, dropped.copy()))
        _apply_results(state, pairings)
    return state, all_rounds


@pytest.mark.parametrize('n', [4, 5, 6, 8, 16])
def test_swiss_invariants(n):
    state, all_rounds = _run(n)

    seen_pairs = set()
    for pairings, dropped in all_rounds:
        active = set(range(1, n + 1)) - dropped
        byes = [a for a, b in pairings if b is None]
        non_bye_refs = []

        assert len(byes) <= 1
        if len(active) % 2 == 0:
            assert byes == []
        else:
            assert len(byes) == 1

        for a, b in pairings:
            assert a not in dropped
            if b is None:
                continue
            assert b not in dropped
            non_bye_refs.extend((a, b))

            key = frozenset((a, b))
            assert key not in seen_pairs, f"rematch {a}-{b}"
            seen_pairs.add(key)

        placed = non_bye_refs + byes
        assert sorted(placed) == sorted(active)
        assert len(placed) == len(set(placed))


def test_bye_goes_to_low_standing_not_leader():
    """The bye must not land on the points leader while lower players qualify."""
    n = 5
    _, all_rounds = _run(n)
    # Reconstruct standings entering each round to check the bye recipient.
    state = {
        ref: {'points': 0.0, 'opponents': set(), 'received_bye': False}
        for ref in range(1, n + 1)
    }
    saw_bye = False
    for pairings, _dropped in all_rounds:
        byes = [a for a, b in pairings if b is None]
        if byes:
            saw_bye = True
            bye_ref = byes[0]
            leader_pts = max(s['points'] for s in state.values())
            # There exist lower-standing eligible players, so the leader
            # (max points) should not be the one receiving the bye.
            assert state[bye_ref]['points'] < leader_pts or all(
                s['points'] == leader_pts for s in state.values()
            )
        _apply_results(state, pairings)
    assert saw_bye  # odd field must produce a bye each round


def test_no_second_bye_while_others_eligible():
    """No player gets a second bye while a bye-eligible player still has none."""
    n = 5
    _, all_rounds = _run(n)
    got_bye = set()
    for pairings, _dropped in all_rounds:
        byes = [a for a, b in pairings if b is None]
        for bye_ref in byes:
            if bye_ref in got_bye:
                # Only acceptable if literally everyone already had a bye.
                assert got_bye >= set(range(1, n + 1))
            got_bye.add(bye_ref)


def test_determinism():
    """Same inputs produce identical pairings every call."""
    engine = _engine()
    players = [
        PairingPlayer(ref=r, points=float(r % 3), opponents=frozenset())
        for r in range(1, 9)
    ]
    first = engine.pair_round(players, {})
    for _ in range(5):
        assert engine.pair_round(players, {}) == first


def test_dropped_player_never_paired():
    n = 6
    state, all_rounds = _run(n, drops_by_round={1: {3}, 2: {5}})
    for pairings, dropped in all_rounds:
        refs = set()
        for a, b in pairings:
            refs.add(a)
            if b is not None:
                refs.add(b)
        assert refs.isdisjoint(dropped)


def test_none_config_treated_as_empty():
    engine = _engine()
    players = [
        PairingPlayer(ref=1, points=0.0),
        PairingPlayer(ref=2, points=0.0),
    ]
    assert engine.pair_round(players, None) == [(1, 2)] or engine.pair_round(
        players, None
    ) == [(2, 1)]


def test_fewer_than_two_active():
    engine = _engine()
    assert engine.pair_round([], {}) == []
    one = [PairingPlayer(ref=7, points=0.0, can_bye=True, received_bye=False)]
    assert engine.pair_round(one, {}) == [(7, None)]
    ineligible = [PairingPlayer(ref=7, can_bye=False)]
    assert engine.pair_round(ineligible, {}) == []
    already = [PairingPlayer(ref=7, received_bye=True)]
    assert engine.pair_round(already, {}) == []
    assert engine.pair_round([PairingPlayer(ref=1, dropped=True)], {}) == []
