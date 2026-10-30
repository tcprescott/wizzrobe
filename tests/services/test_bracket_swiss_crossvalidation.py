"""Cross-validate our Swiss engine against bbpPairings (a real FIDE-grade Dutch
engine) at the level of *hard constraints*, not identical output.

bbpPairings implements the Dutch system, so its pairings differ from our
``swisspair``-based engine's. What both must nonetheless satisfy for the same
tournament state is a set of hard constraints: no rematch against pairing
history, at most one bye, and every active player paired exactly once. Serializing
each scenario to TRF(x), feeding it to the real binary, and checking that both
engines' pairings are legal proves (a) our TRF round-trips through a genuine FIDE
parser and (b) both engines legally pair the same instance.

The always-run tests need no binary: they check the TRF serialization shape and
that our engine pairs every scenario legally. The binary-gated tests run only
when ``BBPPAIRINGS_BIN`` points at a built ``bbpPairings`` executable.
"""

from __future__ import annotations

import os
import subprocess
from typing import Dict, List, Optional, Set, Tuple

import pytest

from application.services.bracket_engines import get_bracket_engine
from application.services.bracket_engines.base import PairingPlayer

from ._trf import TrfPlayer, parse_bbp_pairings, to_trf

# Each scenario is a list of played rounds; a round is a list of pairs. A pair
# (white, black) is a decisive game the *white* (first) player won; (num, None)
# is a full-point bye. Results and points are derived deterministically from
# these, so there is no randomness anywhere.
Pair = Tuple[int, Optional[int]]
Scenario = Tuple[str, int, List[List[Pair]]]

SCENARIOS: List[Scenario] = [
    ('4p_1r', 4, [[(1, 2), (3, 4)]]),
    ('4p_2r', 4, [[(1, 2), (3, 4)], [(1, 3), (2, 4)]]),
    ('6p_1r', 6, [[(1, 2), (3, 4), (5, 6)]]),
    ('6p_2r', 6, [[(1, 2), (3, 4), (5, 6)], [(1, 3), (5, 2), (4, 6)]]),
    ('8p_1r', 8, [[(1, 2), (3, 4), (5, 6), (7, 8)]]),
    ('5p_1r_bye', 5, [[(1, 2), (3, 4), (5, None)]]),
]


def _build(
    n_players: int, rounds: List[List[Pair]]
) -> Tuple[List[TrfPlayer], List[PairingPlayer], Set[int], Dict[int, Set[int]]]:
    """Derive TRF rows, engine inputs, active refs, and the opponent map."""
    points = {n: 0.0 for n in range(1, n_players + 1)}
    history: Dict[int, List[Tuple[int, str, str]]] = {
        n: [] for n in range(1, n_players + 1)
    }
    opponents: Dict[int, Set[int]] = {n: set() for n in range(1, n_players + 1)}
    received_bye: Dict[int, bool] = {n: False for n in range(1, n_players + 1)}

    for rnd in rounds:
        for white, black in rnd:
            if black is None:
                points[white] += 1.0
                history[white].append((0, '-', 'U'))
                received_bye[white] = True
            else:
                points[white] += 1.0
                history[white].append((black, 'w', '1'))
                history[black].append((white, 'b', '0'))
                opponents[white].add(black)
                opponents[black].add(white)

    trf_players = [
        TrfPlayer(number=n, points=points[n], history=history[n])
        for n in range(1, n_players + 1)
    ]
    pairing_players = [
        PairingPlayer(
            ref=n,
            points=points[n],
            opponents=frozenset(opponents[n]),
            received_bye=received_bye[n],
            can_bye=True,
        )
        for n in range(1, n_players + 1)
    ]
    active = set(range(1, n_players + 1))
    return trf_players, pairing_players, active, opponents


def _assert_valid(
    pairings: List[Pair], active: Set[int], opponents: Dict[int, Set[int]]
) -> None:
    """Assert a round's pairings satisfy the Swiss hard constraints."""
    seen: List[int] = []
    byes = 0
    for a, b in pairings:
        seen.append(a)
        if b is None:
            byes += 1
        else:
            seen.append(b)
            assert b not in opponents[a], f'rematch {a} vs {b}'
            assert a not in opponents[b], f'rematch {b} vs {a}'
    assert byes <= 1, f'more than one bye: {byes}'
    assert len(seen) == len(set(seen)), f'player paired twice: {seen}'
    assert set(seen) == active, f'not all active players covered: {set(seen)} != {active}'


def test_trf_serialization_shape():
    """Hand-checked column layout for a two-round, four-player state."""
    trf_players, _, _, _ = _build(4, SCENARIOS[1][2])
    text = to_trf(trf_players, rounds_played=2, total_rounds=3)
    lines = text.splitlines()

    assert lines[0].startswith('012 ')
    assert lines[-1] == 'XXR 3'

    p1 = next(ln for ln in lines if ln.startswith('001') and ln[4:8] == '   1')
    # cols 5-8 pairing number; cols 81-84 points (won both rounds -> 2.0)
    assert p1[4:8] == '   1'
    assert p1[80:84] == ' 2.0'
    # round blocks start at col 91: player 1 was white vs 2 (r1) then vs 3 (r2)
    assert p1[90:] == '    2 w 1     3 w 1'

    # the bye row uses the 0000 / '-' / 'U' sentinel
    bye_players, _, _, _ = _build(5, SCENARIOS[5][2])
    bye_text = to_trf(bye_players, rounds_played=1, total_rounds=2)
    p5 = next(ln for ln in bye_text.splitlines() if ln.startswith('001') and ln[4:8] == '   5')
    assert p5[90:] == ' 0000 - U'


@pytest.mark.parametrize('name,n,rounds', SCENARIOS, ids=[s[0] for s in SCENARIOS])
def test_our_engine_produces_valid_pairings(name, n, rounds):
    _, pairing_players, active, opponents = _build(n, rounds)
    engine = get_bracket_engine('swiss')()
    pairings = engine.pair_round(pairing_players, {})
    _assert_valid(pairings, active, opponents)


@pytest.mark.skipif(
    not (os.environ.get('BBPPAIRINGS_BIN') and os.path.exists(os.environ['BBPPAIRINGS_BIN'])),
    reason='bbpPairings binary not available',
)
@pytest.mark.parametrize('name,n,rounds', SCENARIOS, ids=[s[0] for s in SCENARIOS])
def test_crossvalidate_against_bbppairings(name, n, rounds, tmp_path):
    trf_players, pairing_players, active, opponents = _build(n, rounds)
    rounds_played = len(rounds)

    trf_text = to_trf(trf_players, rounds_played, total_rounds=rounds_played + 1)
    inp = tmp_path / f'{name}.trf'
    out = tmp_path / f'{name}.out'
    inp.write_text(trf_text)

    result = subprocess.run(
        [os.environ['BBPPAIRINGS_BIN'], '--dutch', str(inp), '-p', str(out)],
        timeout=15,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f'bbpPairings failed: {result.stdout} {result.stderr}'
    assert out.exists(), 'bbpPairings produced no output file'

    bbp_pairings = parse_bbp_pairings(out.read_text())
    # bbp's Dutch pairing must itself be legal for this instance...
    _assert_valid(bbp_pairings, active, opponents)

    # ...and so must our engine's independent pairing of the same instance.
    engine = get_bracket_engine('swiss')()
    our_pairings = engine.pair_round(pairing_players, {})
    _assert_valid(our_pairings, active, opponents)
