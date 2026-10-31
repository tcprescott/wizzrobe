"""Unit tests for the shared standings & tiebreakers module."""

from __future__ import annotations

import pytest

from application.services.bracket_engines.standings import (
    ResultRow,
    Standing,
    StandingsConfig,
    compute_standings,
    standings_config_from,
)


def _by_ref(standings: list[Standing]) -> dict[int, Standing]:
    return {s.ref: s for s in standings}


def test_points_totals_wins_draws_losses_byes():
    refs = [1, 2, 3, 4]
    results = [
        ResultRow(1, 2, winner=1),
        ResultRow(1, 3, winner=None),
        ResultRow(4, None, winner=4),
        ResultRow(2, 3, winner=3),
    ]
    config = StandingsConfig(win_points=3, draw_points=1, loss_points=0, bye_points=3)
    s = _by_ref(compute_standings(refs, results, config))

    assert s[1].points == 3 + 1
    assert s[1].wins == 1 and s[1].draws == 1
    assert s[2].points == 0
    assert s[2].losses == 2
    assert s[3].points == 1 + 3
    assert s[4].points == 3 and s[4].byes == 1


def test_ranks_and_ordering_sorted():
    refs = [1, 2, 3]
    results = [
        ResultRow(1, 2, winner=1),
        ResultRow(1, 3, winner=1),
        ResultRow(2, 3, winner=2),
    ]
    out = compute_standings(refs, results, StandingsConfig())
    assert [s.ref for s in out] == [1, 2, 3]
    assert [s.rank for s in out] == [1, 2, 3]
    assert all(s.tied_with == () for s in out)


def test_buchholz_breaks_equal_points():
    refs = [1, 2, 3, 4, 5]
    results = [
        ResultRow(1, 3, winner=1),
        ResultRow(4, 1, winner=4),
        ResultRow(2, 5, winner=2),
        ResultRow(4, 2, winner=4),
        ResultRow(3, 5, winner=3),
    ]
    config = StandingsConfig(win_points=1, tiebreakers=('buchholz',))
    s = _by_ref(compute_standings(refs, results, config))

    assert s[1].points == s[2].points == 1
    assert s[1].tiebreakers['buchholz'] == 3
    assert s[2].tiebreakers['buchholz'] == 2
    assert s[1].rank < s[2].rank
    assert s[1].tied_with == () and s[2].tied_with == ()


def test_omw_with_floor_applied():
    # The assertion on 1 exercises the floor: 3's true win rate is 0, but OMW uses omw_floor.
    refs = [1, 2, 3, 4, 5, 6]
    results = [
        ResultRow(3, 5, winner=5),
        ResultRow(3, 6, winner=6),
        ResultRow(1, 3, winner=1),
        ResultRow(4, 5, winner=4),
        ResultRow(4, 6, winner=4),
        ResultRow(2, 4, winner=2),
    ]
    config = StandingsConfig(win_points=1, tiebreakers=('omw',), omw_floor=1.0 / 3.0)
    s = _by_ref(compute_standings(refs, results, config))

    assert s[1].points == s[2].points == 1
    assert s[1].tiebreakers['omw'] == pytest.approx(1.0 / 3.0)
    assert s[2].tiebreakers['omw'] == pytest.approx(2.0 / 3.0)
    assert s[2].rank < s[1].rank


def test_head_to_head_breaks_two_way_tie():
    refs = [1, 2, 3, 4, 5]
    results = [
        ResultRow(1, 3, winner=1),
        ResultRow(2, 3, winner=2),
        ResultRow(1, 4, winner=4),
        ResultRow(2, 4, winner=4),
        ResultRow(1, 5, winner=5),
        ResultRow(2, 5, winner=2),
        ResultRow(1, 2, winner=1),
    ]
    config = StandingsConfig(win_points=1, tiebreakers=('head_to_head',))
    s = _by_ref(compute_standings(refs, results, config))

    assert s[1].points == s[2].points == 2
    assert s[1].rank < s[2].rank
    assert s[1].tied_with == () and s[2].tied_with == ()


def test_unresolved_tie_shares_rank_and_populates_tied_with():
    refs = [1, 2, 3]
    results = [
        ResultRow(1, 2, winner=1),
        ResultRow(2, 3, winner=2),
        ResultRow(3, 1, winner=3),
    ]
    config = StandingsConfig(
        win_points=1, tiebreakers=('buchholz', 'omw', 'head_to_head')
    )
    s = _by_ref(compute_standings(refs, results, config))

    assert {s[r].points for r in refs} == {1}
    assert all(s[r].rank == 1 for r in refs)
    assert s[1].tied_with == (2, 3)
    assert s[2].tied_with == (1, 3)
    assert s[3].tied_with == (1, 2)


def test_head_to_head_only_splits_when_decisive():
    refs = [10, 11, 12, 13]
    results = [
        ResultRow(10, 13, winner=10),
        ResultRow(11, 13, winner=11),
        ResultRow(12, 13, winner=12),
        ResultRow(10, 11, winner=10),
        ResultRow(11, 12, winner=11),
        ResultRow(10, 12, winner=10),
        ResultRow(10, 13, winner=13),
    ]
    # Not actually a point tie (3/2/1 points): checks head-to-head never overrides a clear points order.
    config = StandingsConfig(win_points=1, tiebreakers=('head_to_head',))
    s = _by_ref(compute_standings(refs, results, config))
    assert s[10].rank < s[11].rank < s[12].rank < s[13].rank


def test_result_referencing_unknown_ref_raises():
    with pytest.raises(ValueError):
        compute_standings([1, 2], [ResultRow(1, 99, winner=1)], StandingsConfig())


def test_self_pairing_rejected():
    with pytest.raises(ValueError):
        compute_standings([1], [ResultRow(1, 1, winner=1)], StandingsConfig())


def test_standings_config_from_drops_unknown_tiebreakers():
    """Rows persisted before the config schema validated tiebreaker keys must
    still render: an unknown key is dropped rather than raised on."""
    config = standings_config_from({'tiebreakers': ['buchholz', 'h2h']})
    assert config.tiebreakers == ('buchholz',)


def test_standings_config_from_reads_scoring_parameters():
    config = standings_config_from({'win_points': 3.0, 'draw_points': 1.0})
    assert (config.win_points, config.draw_points) == (3.0, 1.0)
    assert config.loss_points == 0.0
