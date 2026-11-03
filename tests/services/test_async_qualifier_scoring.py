"""Unit tests for the async-qualifier par/score/leaderboard math (pure, no DB)."""

from application.services.async_qualifier.async_qualifier_scoring import (
    ScoredRun,
    build_leaderboard,
    compute_par,
    compute_score,
)


class TestComputePar:
    def test_mean_of_n_fastest(self):
        assert compute_par([900, 100, 140, 120], sample_size=3) == 120

    def test_fewer_than_sample_uses_all(self):
        assert compute_par([100, 200], sample_size=5) == 150

    def test_no_runs_is_none(self):
        assert compute_par([], sample_size=3) is None

    def test_ignores_nonpositive(self):
        assert compute_par([0, -5, 120], sample_size=3) == 120


class TestComputeScore:
    def test_at_par_is_100(self):
        assert compute_score(120, 120) == 100.0

    def test_twice_par_is_zero(self):
        assert compute_score(240, 120) == 0.0

    def test_fast_run_caps_at_105(self):
        assert compute_score(1, 120) == 105.0

    def test_slow_run_floors_at_zero(self):
        assert compute_score(10_000, 120) == 0.0

    def test_missing_inputs_none(self):
        assert compute_score(None, 120) is None
        assert compute_score(120, None) is None
        assert compute_score(120, 0) is None


class TestLeaderboard:
    def test_fills_slots_and_pads_missing_with_zero(self):
        entries = build_leaderboard(
            pool_ids=[1, 2], runs_per_pool=1,
            scored_runs=[
                ScoredRun(1, 'a', 1, 100.0),
                ScoredRun(1, 'a', 2, 80.0),
                ScoredRun(2, 'b', 1, 90.0),
            ],
        )
        by_user = {e.user_id: e for e in entries}
        assert by_user[1].actual == 180.0
        assert by_user[1].estimate == 180.0
        assert by_user[2].actual == 90.0
        assert by_user[2].estimate == 180.0
        assert entries[0].user_id == 1

    def test_caps_scores_per_pool_at_runs_per_pool(self):
        entries = build_leaderboard(
            pool_ids=[1], runs_per_pool=1,
            scored_runs=[ScoredRun(1, 'a', 1, 40.0), ScoredRun(1, 'a', 1, 95.0)],
        )
        assert entries[0].actual == 95.0
        assert entries[0].slots_filled == 1

    def test_ignores_runs_in_unknown_pools(self):
        entries = build_leaderboard(
            pool_ids=[1], runs_per_pool=1,
            scored_runs=[ScoredRun(1, 'a', 99, 100.0)],
        )
        assert entries == []


class TestOpenPools:
    """A pool nobody self-paced could draw from counts only for whoever raced it."""

    def test_a_closed_pool_does_not_inflate_everyone_else(self):
        board = build_leaderboard(
            pool_ids=[10, 20], runs_per_pool=1, open_pool_ids=[10],
            scored_runs=[ScoredRun(1, 'Async Only', 10, 100.0)],
        )
        assert board[0].slots_total == 1, 'only the open pool is theirs to fill'
        assert board[0].estimate == board[0].actual

    def test_a_closed_pool_counts_for_the_racer_who_filled_it(self):
        board = build_leaderboard(
            pool_ids=[10, 20], runs_per_pool=1, open_pool_ids=[10],
            scored_runs=[ScoredRun(1, 'Both', 10, 100.0), ScoredRun(1, 'Both', 20, 90.0)],
        )
        assert board[0].slots_total == 2
        assert board[0].actual == 190.0

    def test_omitting_open_pool_ids_keeps_every_pool_open(self):
        board = build_leaderboard(
            pool_ids=[10, 20], runs_per_pool=1,
            scored_runs=[ScoredRun(1, 'A', 10, 100.0)],
        )
        assert board[0].slots_total == 2

    def test_an_open_pool_id_outside_pool_ids_is_ignored(self):
        board = build_leaderboard(
            pool_ids=[10], runs_per_pool=1, open_pool_ids=[10, 999],
            scored_runs=[ScoredRun(1, 'A', 10, 100.0)],
        )
        assert board[0].slots_total == 1


class TestCompetitionRanks:
    def test_a_clean_board_ranks_one_two_three(self):
        board = build_leaderboard(
            pool_ids=[10], runs_per_pool=1,
            scored_runs=[ScoredRun(1, 'A', 10, 100.0), ScoredRun(2, 'B', 10, 90.0),
                         ScoredRun(3, 'C', 10, 80.0)],
        )
        assert [e.rank for e in board] == [1, 2, 3]

    def test_a_tie_shares_a_rank_and_the_next_one_skips(self):
        board = build_leaderboard(
            pool_ids=[10], runs_per_pool=1,
            scored_runs=[ScoredRun(1, 'A', 10, 100.0), ScoredRun(2, 'B', 10, 100.0),
                         ScoredRun(3, 'C', 10, 100.0), ScoredRun(4, 'D', 10, 50.0)],
        )
        assert [e.rank for e in board] == [1, 1, 1, 4], (
            'three tied on 100 all rank 1, and the next distinct total is 4th'
        )

    def test_everyone_tied_shares_first(self):
        board = build_leaderboard(
            pool_ids=[10], runs_per_pool=1,
            scored_runs=[ScoredRun(i, chr(64 + i), 10, 10.0) for i in (1, 2, 3)],
        )
        assert [e.rank for e in board] == [1, 1, 1]

    def test_a_single_entrant_ranks_first(self):
        board = build_leaderboard(pool_ids=[10], runs_per_pool=1,
                                  scored_runs=[ScoredRun(1, 'A', 10, 1.0)])
        assert board[0].rank == 1

    def test_an_empty_board_has_no_ranks_to_assign(self):
        assert build_leaderboard(pool_ids=[10], runs_per_pool=1, scored_runs=[]) == []


class TestSpentSlotZeros:
    """The service passes a spent-but-unscoreable slot in as a zero; check the maths."""

    def test_a_zero_fills_its_slot_without_moving_the_total(self):
        board = build_leaderboard(
            pool_ids=[10], runs_per_pool=2,
            scored_runs=[ScoredRun(1, 'A', 10, 100.0), ScoredRun(1, 'A', 10, 0.0)],
        )
        assert board[0].actual == 100.0
        assert board[0].slots_filled == 2
        assert board[0].estimate == 100.0, (
            'with both slots spent there is nothing left to project onto'
        )

    def test_a_player_who_only_spent_zeros_ranks_last_rather_than_vanishing(self):
        board = build_leaderboard(
            pool_ids=[10], runs_per_pool=1,
            scored_runs=[ScoredRun(1, 'Scored', 10, 50.0), ScoredRun(2, 'Zeroed', 10, 0.0)],
        )
        assert [(e.username, e.actual, e.rank) for e in board] == [
            ('Scored', 50.0, 1), ('Zeroed', 0.0, 2)]

    def test_the_per_pool_cap_keeps_the_best_when_a_zero_competes(self):
        board = build_leaderboard(
            pool_ids=[10], runs_per_pool=1,
            scored_runs=[ScoredRun(1, 'A', 10, 0.0), ScoredRun(1, 'A', 10, 80.0)],
        )
        assert board[0].actual == 80.0 and board[0].slots_filled == 1
