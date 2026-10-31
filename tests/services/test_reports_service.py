"""Unit tests for ReportsService pure-function helpers.

Tests that require a DB are skipped here; the session-scoped ``db`` fixture
in conftest.py is available for async DB tests but not used for the pure
computations below.
"""

from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Optional

from application.services.reports_service import (
    CHECK_IN_TO_START,
    DEFAULT_MATCH_DURATION_MIN,
    LATE_PUSH_LIMIT,
    ReportsService,
    event_day_bounds,
)
from application.utils.timezone import EASTERN_TZ, to_local


def make_match(
    *,
    scheduled_at=None,
    seated_at=None,
    started_at=None,
    finished_at=None,
    avg_duration: Optional[int] = None,
    players_per_match: int = 2,
    stage_id=None,
):
    tournament = SimpleNamespace(
        average_match_duration=avg_duration,
        players_per_match=players_per_match,
        name='Test',
    )
    return SimpleNamespace(
        id=1,
        tournament_id=1,
        tournament=tournament,
        scheduled_at=scheduled_at,
        seated_at=seated_at,
        started_at=started_at,
        finished_at=finished_at,
        stage_id=stage_id,
        is_stream_candidate=False,
        players=[],
    )


def to_display(year, month, day, hour=0, minute=0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=EASTERN_TZ)


from tests.factories import utc


def naive(year, month, day, hour=0, minute=0) -> datetime:
    return datetime(year, month, day, hour, minute)


class TestEastern:
    def test_returns_none_for_none(self):
        assert ReportsService._local(None) is None

    def test_naive_treated_as_utc_winter(self):
        # Naive 2025-01-15 19:30 should be interpreted as UTC → 14:30 ET (EST = UTC-5)
        dt = naive(2025, 1, 15, 19, 30)
        result = ReportsService._local(dt)
        assert result.tzinfo is not None
        assert result.hour == 14
        assert result.minute == 30

    def test_naive_treated_as_utc_summer(self):
        # Naive 2025-07-15 18:00 should be interpreted as UTC → 14:00 ET (EDT = UTC-4)
        dt = naive(2025, 7, 15, 18, 0)
        result = ReportsService._local(dt)
        assert result.hour == 14

    def test_aware_utc_is_converted(self):
        dt = utc(2025, 1, 15, 19, 30)
        result = ReportsService._local(dt)
        assert result.hour == 14
        assert result.minute == 30

    def test_already_eastern_is_unchanged(self):
        dt = to_display(2025, 1, 15, 14, 30)
        result = ReportsService._local(dt)
        assert result.hour == 14
        assert result.minute == 30


class TestMatchWindow:
    def test_no_scheduled_at_returns_none(self):
        assert ReportsService._match_window(make_match()) is None

    def test_no_seated_start_is_one_hour_before_scheduled(self):
        sched = to_display(2025, 10, 23, 14, 0)
        m = make_match(scheduled_at=sched)
        ws, we = ReportsService._match_window(m)
        assert ws == to_display(2025, 10, 23, 13, 0)

    def test_seated_at_overrides_start(self):
        sched = to_display(2025, 10, 23, 14, 0)
        seated = to_display(2025, 10, 23, 13, 30)
        m = make_match(scheduled_at=sched, seated_at=seated)
        ws, _ = ReportsService._match_window(m)
        assert ws == seated

    def test_finished_at_sets_end(self):
        sched = to_display(2025, 10, 23, 14, 0)
        finished = to_display(2025, 10, 23, 15, 45)
        m = make_match(scheduled_at=sched, finished_at=finished)
        _, we = ReportsService._match_window(m)
        assert we == finished

    def test_no_finished_uses_tournament_avg_duration(self):
        sched = to_display(2025, 10, 23, 14, 0)
        m = make_match(scheduled_at=sched, avg_duration=60)
        ws, we = ReportsService._match_window(m)
        assert (we - ws) == timedelta(hours=1 + 1)

    def test_no_finished_defaults_to_90_min_when_no_tournament_avg(self):
        sched = to_display(2025, 10, 23, 14, 0)
        m = make_match(scheduled_at=sched, avg_duration=None)
        ws, we = ReportsService._match_window(m)
        assert (we - ws) == timedelta(minutes=60 + DEFAULT_MATCH_DURATION_MIN)

    def test_naive_scheduled_at_treated_as_utc(self):
        sched = naive(2025, 1, 15, 19, 0)
        m = make_match(scheduled_at=sched)
        ws, _ = ReportsService._match_window(m)
        assert ws.hour == 13

    def test_late_unchecked_match_slides_with_now(self):
        sched = to_display(2025, 10, 23, 14, 0)
        now = to_display(2025, 10, 23, 14, 40)
        m = make_match(scheduled_at=sched, avg_duration=60)
        ws, we = ReportsService._match_window(m, now=now)
        assert ws == to_display(2025, 10, 23, 13, 0)
        assert we == to_display(2025, 10, 23, 15, 40)

    def test_not_yet_late_match_keeps_its_slot(self):
        sched = to_display(2025, 10, 23, 14, 0)
        m = make_match(scheduled_at=sched, avg_duration=60)
        _, we = ReportsService._match_window(m, now=to_display(2025, 10, 23, 13, 30))
        assert we == to_display(2025, 10, 23, 15, 0)

    def test_late_slide_stops_at_limit(self):
        sched = to_display(2025, 10, 23, 14, 0)
        m = make_match(scheduled_at=sched, avg_duration=60)
        _, we = ReportsService._match_window(m, now=to_display(2025, 10, 24, 9, 0))
        assert we == sched + LATE_PUSH_LIMIT + timedelta(minutes=60)

    def test_late_check_in_runs_from_check_in(self):
        sched = to_display(2025, 10, 23, 14, 0)
        seated = to_display(2025, 10, 23, 14, 25)
        m = make_match(scheduled_at=sched, seated_at=seated, avg_duration=60)
        ws, we = ReportsService._match_window(m, now=to_display(2025, 10, 23, 14, 50))
        assert ws == seated
        assert we == to_display(2025, 10, 23, 15, 25)

    def test_started_match_ignores_now(self):
        sched = to_display(2025, 10, 23, 14, 0)
        started = to_display(2025, 10, 23, 14, 10)
        m = make_match(scheduled_at=sched, seated_at=sched, started_at=started, avg_duration=60)
        _, we = ReportsService._match_window(m, now=to_display(2025, 10, 23, 16, 0))
        assert we == to_display(2025, 10, 23, 15, 10)

    def test_setup_pushes_unchecked_match_end(self):
        sched = to_display(2025, 10, 23, 14, 0)
        m = make_match(scheduled_at=sched, avg_duration=60)
        ws, we = ReportsService._match_window(
            m, now=to_display(2025, 10, 23, 13, 30), setup=CHECK_IN_TO_START,
        )
        assert ws == to_display(2025, 10, 23, 13, 0)
        assert we == to_display(2025, 10, 23, 15, 15)

    def test_setup_runs_from_check_in(self):
        sched = to_display(2025, 10, 23, 14, 0)
        seated = to_display(2025, 10, 23, 14, 25)
        m = make_match(scheduled_at=sched, seated_at=seated, avg_duration=60)
        _, we = ReportsService._match_window(
            m, now=to_display(2025, 10, 23, 14, 30), setup=CHECK_IN_TO_START,
        )
        assert we == to_display(2025, 10, 23, 15, 40)

    def test_setup_counts_early_check_in_from_slot(self):
        sched = to_display(2025, 10, 23, 14, 0)
        seated = to_display(2025, 10, 23, 13, 50)
        m = make_match(scheduled_at=sched, seated_at=seated, avg_duration=60)
        ws, we = ReportsService._match_window(m, setup=CHECK_IN_TO_START)
        assert ws == seated
        assert we == to_display(2025, 10, 23, 15, 15)

    def test_setup_slides_with_late_unchecked_match(self):
        sched = to_display(2025, 10, 23, 14, 0)
        m = make_match(scheduled_at=sched, avg_duration=60)
        _, we = ReportsService._match_window(
            m, now=to_display(2025, 10, 23, 14, 40), setup=CHECK_IN_TO_START,
        )
        assert we == to_display(2025, 10, 23, 15, 55)

    def test_setup_ignored_once_started(self):
        sched = to_display(2025, 10, 23, 14, 0)
        started = to_display(2025, 10, 23, 14, 10)
        m = make_match(scheduled_at=sched, seated_at=sched, started_at=started, avg_duration=60)
        _, we = ReportsService._match_window(m, setup=CHECK_IN_TO_START)
        assert we == to_display(2025, 10, 23, 15, 10)

    def test_end_not_before_start(self):
        sched = to_display(2025, 10, 23, 14, 0)
        seated = to_display(2025, 10, 23, 14, 0)
        finished = to_display(2025, 10, 23, 13, 0)
        m = make_match(scheduled_at=sched, seated_at=seated, finished_at=finished)
        ws, we = ReportsService._match_window(m)
        assert we >= ws


class TestAutoIntervalMinutes:
    def test_less_than_24h_gives_15min(self):
        s = to_display(2025, 10, 23, 0, 0)
        e = s + timedelta(hours=12)
        assert ReportsService._auto_interval_minutes(s, e) == 15

    def test_exactly_24h_gives_15min(self):
        s = to_display(2025, 10, 23, 0, 0)
        e = s + timedelta(hours=24)
        assert ReportsService._auto_interval_minutes(s, e) == 15

    def test_25h_gives_30min(self):
        s = to_display(2025, 10, 23, 0, 0)
        e = s + timedelta(hours=25)
        assert ReportsService._auto_interval_minutes(s, e) == 30

    def test_exactly_72h_gives_30min(self):
        s = to_display(2025, 10, 23, 0, 0)
        e = s + timedelta(hours=72)
        assert ReportsService._auto_interval_minutes(s, e) == 30

    def test_more_than_72h_gives_60min(self):
        s = to_display(2025, 10, 23, 0, 0)
        e = s + timedelta(hours=96)
        assert ReportsService._auto_interval_minutes(s, e) == 60


class TestPeakTimes:
    def _times(self):
        return [to_display(2025, 10, 23, h) for h in range(5)]

    def test_returns_top_n(self):
        intervals = self._times()
        counts = [3, 7, 2, 9, 5]
        peaks = ReportsService.peak_times(intervals, counts, top_n=3)
        assert len(peaks) == 3

    def test_sorted_descending(self):
        intervals = self._times()
        counts = [3, 7, 2, 9, 5]
        peaks = ReportsService.peak_times(intervals, counts, top_n=3)
        assert [p[1] for p in peaks] == [9, 7, 5]

    def test_all_zeros_returns_zeros(self):
        intervals = self._times()
        counts = [0, 0, 0, 0, 0]
        peaks = ReportsService.peak_times(intervals, counts, top_n=2)
        assert all(p[1] == 0 for p in peaks)

    def test_top_n_larger_than_list(self):
        intervals = self._times()[:2]
        counts = [5, 3]
        peaks = ReportsService.peak_times(intervals, counts, top_n=10)
        assert len(peaks) == 2


class TestEventDayBounds:
    def test_start_is_local_midnight(self):
        from datetime import date
        s, e = event_day_bounds(date(2025, 10, 23))
        assert s == to_display(2025, 10, 23, 0, 0)
        assert s.utcoffset() == timedelta(0)
        assert to_local(s).hour == 0

    def test_end_is_next_midnight(self):
        from datetime import date
        s, e = event_day_bounds(date(2025, 10, 23))
        assert e == to_display(2025, 10, 24, 0, 0)

    def test_window_is_24_hours(self):
        from datetime import date
        s, e = event_day_bounds(date(2025, 10, 23))
        assert (e - s) == timedelta(hours=24)
