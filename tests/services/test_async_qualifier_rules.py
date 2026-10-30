"""Unit tests for the async-qualifier pure rule helpers (no DB).

The claim classifier is the load-bearing one: it decides which submitted finish
times are refused outright, which the runner is asked about, and which pass
without comment. ``measured`` is an upper bound on the real run — the timer runs
from the draw through reading the seed and the gap before submitting — so a claim
*under* the measurement is normal and only a large shortfall is worth a question.
"""

from datetime import datetime, timedelta, timezone

from application.services.async_qualifier.async_qualifier_rules import (
    CLOCK_GRACE_SECONDS,
    IMPLAUSIBLE_DRIFT_SECONDS,
    ClaimVerdict,
    classify_claim,
    describe_claim,
    measure_elapsed,
)


class TestClassifyClaim:
    def test_no_measurement_is_never_a_discrepancy(self):
        assert classify_claim(5025, None) is ClaimVerdict.OK

    def test_a_claim_matching_the_clock_is_ok(self):
        assert classify_claim(3600, 3620) is ClaimVerdict.OK

    def test_a_claim_under_the_clock_by_less_than_the_threshold_is_ok(self):
        assert classify_claim(3600, 3600 + IMPLAUSIBLE_DRIFT_SECONDS - 1) is ClaimVerdict.OK

    def test_a_large_shortfall_is_implausible(self):
        # The dropped-segment case: typed 0:14:22 on a run the server timed 1:14:22.
        assert classify_claim(862, 4462) is ClaimVerdict.IMPLAUSIBLE

    def test_exactly_the_drift_threshold_is_implausible(self):
        assert classify_claim(3600, 3600 + IMPLAUSIBLE_DRIFT_SECONDS) is ClaimVerdict.IMPLAUSIBLE

    def test_a_claim_longer_than_the_run_is_impossible(self):
        assert classify_claim(4462, 862) is ClaimVerdict.IMPOSSIBLE

    def test_exactly_the_clock_grace_is_still_ok(self):
        assert classify_claim(3600 + CLOCK_GRACE_SECONDS, 3600) is ClaimVerdict.OK

    def test_one_second_past_the_clock_grace_is_impossible(self):
        assert classify_claim(3601 + CLOCK_GRACE_SECONDS, 3600) is ClaimVerdict.IMPOSSIBLE


class TestDescribeClaim:
    def test_names_both_numbers(self):
        message = describe_claim(4462, 862)
        assert '0:14:22' in message      # measured
        assert '1:14:22' in message      # claimed
        assert 'longer than the run itself' in message


class TestMeasureElapsed:
    def test_unstamped_run_has_no_measurement(self):
        assert measure_elapsed(None) is None

    def test_measures_from_an_aware_start(self):
        now = datetime.now(timezone.utc)
        assert measure_elapsed(now - timedelta(seconds=90), now=now) == 90

    def test_naive_start_is_read_as_utc(self):
        now = datetime.now(timezone.utc)
        naive = (now - timedelta(seconds=90)).replace(tzinfo=None)
        assert measure_elapsed(naive, now=now) == 90

    def test_a_start_in_the_future_clamps_to_zero(self):
        now = datetime.now(timezone.utc)
        assert measure_elapsed(now + timedelta(seconds=30), now=now) == 0
