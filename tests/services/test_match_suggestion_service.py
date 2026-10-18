"""Tests for MatchSuggestionService (unit, no DB)."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from application.services.match.match_suggestion_service import MatchSuggestionService

EASTERN_TZ = timezone(timedelta(hours=-5), 'EST')


def _local(hour, day=15, month=1):
    return datetime(2026, month, day, hour, 0, tzinfo=EASTERN_TZ)


def make_match(scheduled_hour=10, player_count=2, duration_min=90, tournament_duration=None,
               user_ids=None, match_id=None, day=15):
    t = SimpleNamespace(average_match_duration=tournament_duration, name='Test')
    ids = list(user_ids) if user_ids is not None else [None] * player_count
    return SimpleNamespace(
        id=match_id,
        scheduled_at=_local(scheduled_hour, day).astimezone(timezone.utc),
        players=[SimpleNamespace(user_id=uid) for uid in ids],
        tournament=t,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def service():
    svc = object.__new__(MatchSuggestionService)
    svc.availability_repository = MagicMock()
    svc.bracket_repository = MagicMock()
    return svc


# ---------------------------------------------------------------------------
# _round_up_to_interval (static)
# ---------------------------------------------------------------------------


class TestRoundUpToInterval:
    def test_already_on_boundary(self):
        dt = _local(10, 15)  # 10:00 -> on boundary
        result = MatchSuggestionService._round_up_to_interval(dt)
        assert result == dt.replace(second=0, microsecond=0)

    def test_rounds_up_to_next_30(self):
        dt = _local(10, 15).replace(minute=10)
        result = MatchSuggestionService._round_up_to_interval(dt)
        assert result.minute == 30

    def test_rounds_up_crossing_hour(self):
        dt = _local(10, 15).replace(minute=45)
        result = MatchSuggestionService._round_up_to_interval(dt)
        assert result.hour == 11
        assert result.minute == 0


# ---------------------------------------------------------------------------
# _peak_load (static)
# ---------------------------------------------------------------------------


def _bookings(*matches, duration=timedelta(minutes=90)):
    return MatchSuggestionService._bookings(list(matches), duration)


class TestPeakLoad:
    def test_overlapping_matches_add_up(self):
        bookings = _bookings(make_match(10, player_count=2), make_match(11, player_count=4))
        assert MatchSuggestionService._peak_load(bookings, _local(10)) == 6

    def test_sequential_matches_count_once(self):
        first = make_match(10, player_count=4, tournament_duration=60)
        second = make_match(11, player_count=4, tournament_duration=60)
        assert MatchSuggestionService._peak_load(_bookings(first, second), _local(10)) == 4

    def test_no_matches_is_zero(self):
        assert MatchSuggestionService._peak_load([], _local(10)) == 0

    def test_skips_unscheduled_matches(self):
        match = make_match(10)
        match.scheduled_at = None
        assert _bookings(match) == []


# ---------------------------------------------------------------------------
# _generate_candidates
# ---------------------------------------------------------------------------


class TestGenerateCandidates:
    def test_returns_candidates_within_event_window(self, service):
        from datetime import date
        event_start = date(2026, 1, 15)
        event_end = date(2026, 1, 15)
        from_dt = _local(10)  # 10:00 Eastern on Jan 15
        hours_map = {}
        candidates = service._generate_candidates(
            from_dt, None, hours_map, timedelta(hours=2),
            event_start, event_end, EASTERN_TZ,
        )
        # Without configured hours, every event day is open all day
        # from 10:00 we should have many 30-min slots throughout the rest of the day
        assert len(candidates) > 0

    def test_slots_at_30min_intervals(self, service):
        from datetime import date
        event_start = date(2026, 1, 15)
        event_end = date(2026, 1, 15)
        from datetime import time
        hours_map = {event_start: (time(10, 0), time(14, 0))}
        from_dt = _local(10)
        candidates = service._generate_candidates(
            from_dt, _local(14), hours_map, timedelta(hours=1),
            event_start, event_end, EASTERN_TZ,
        )
        if len(candidates) >= 2:
            gap = candidates[1][0] - candidates[0][0]
            assert gap == timedelta(minutes=30)

    def test_respects_event_date_bounds(self, service):
        from datetime import date
        event_start = date(2026, 2, 1)
        event_end = date(2026, 2, 1)
        from_dt = _local(10, day=1, month=2)
        candidates = service._generate_candidates(
            from_dt, None, {}, timedelta(hours=2),
            event_start, event_end, EASTERN_TZ,
        )
        # All candidates should start on the event date
        for slot_start, _ in candidates:
            assert slot_start.date() == event_start


class TestGenerateCandidatesHardEnd:
    """``hard_end`` is the round window's close — a hard bound, not a preference."""

    @staticmethod
    def _generate(service, hard_end, duration=timedelta(hours=1)):
        from datetime import date
        event_start = event_end = date(2026, 1, 15)
        return service._generate_candidates(
            _local(10), None, {}, duration,
            event_start, event_end, EASTERN_TZ, hard_end,
        )

    def test_no_slot_ends_after_the_window(self, service):
        candidates = self._generate(service, _local(14))
        assert candidates
        assert all(slot_end <= _local(14) for _, slot_end in candidates)

    def test_match_must_fit_wholly_inside(self, service):
        # 10:00–11:00 window, 90-minute match: nothing fits, even though the
        # window is open at 10:00.
        assert self._generate(service, _local(11), timedelta(minutes=90)) == []

    def test_window_end_clamps_multi_day_search(self, service):
        from datetime import date
        candidates = service._generate_candidates(
            _local(10), None, {}, timedelta(hours=1),
            date(2026, 1, 15), date(2026, 1, 20), EASTERN_TZ, _local(20, day=16),
        )
        assert candidates
        assert max(s.date() for s, _ in candidates) == date(2026, 1, 16)

    def test_none_leaves_search_unbounded(self, service):
        assert len(self._generate(service, None)) > len(self._generate(service, _local(14)))


# ---------------------------------------------------------------------------
# _round_window
# ---------------------------------------------------------------------------


class TestRoundWindow:
    @staticmethod
    def _service_with(config, round_number=2):
        svc = object.__new__(MatchSuggestionService)
        svc.bracket_repository = MagicMock()
        bracket_match = SimpleNamespace(
            round=round_number, bracket=SimpleNamespace(config=config),
        )

        async def get_match_with_bracket(_id):
            return bracket_match

        svc.bracket_repository.get_match_with_bracket = get_match_with_bracket
        return svc

    @pytest.mark.asyncio
    async def test_no_bracket_match_is_unbounded(self, service):
        assert await service._round_window(None, EASTERN_TZ) == (None, None)

    @pytest.mark.asyncio
    async def test_reads_the_matchups_own_round(self):
        svc = self._service_with({'rounds': {
            '1': {'scheduled_at': '2026-01-15T00:00:00+00:00'},
            '2': {
                'scheduled_at': '2026-01-16T15:00:00+00:00',
                'scheduled_end': '2026-01-16T23:00:00+00:00',
            },
        }})
        start, end = await svc._round_window(7, EASTERN_TZ)
        assert start == datetime(2026, 1, 16, 10, 0, tzinfo=EASTERN_TZ)
        assert end == datetime(2026, 1, 16, 18, 0, tzinfo=EASTERN_TZ)

    @pytest.mark.asyncio
    async def test_half_configured_windows_bound_one_side(self):
        svc = self._service_with({'rounds': {'2': {
            'scheduled_end': '2026-01-16T23:00:00+00:00',
        }}})
        start, end = await svc._round_window(7, EASTERN_TZ)
        assert start is None
        assert end is not None

    @pytest.mark.asyncio
    async def test_unscheduled_round_is_unbounded(self):
        svc = self._service_with({'rounds': {'1': {'best_of': 3}}})
        assert await svc._round_window(7, EASTERN_TZ) == (None, None)

    @pytest.mark.asyncio
    async def test_no_config_at_all_is_unbounded(self):
        svc = self._service_with(None)
        assert await svc._round_window(7, EASTERN_TZ) == (None, None)

    @pytest.mark.asyncio
    async def test_missing_bracket_match_is_unbounded(self, service):
        async def get_match_with_bracket(_id):
            return None

        service.bracket_repository.get_match_with_bracket = get_match_with_bracket
        assert await service._round_window(999, EASTERN_TZ) == (None, None)

    @pytest.mark.asyncio
    async def test_unparseable_stored_value_degrades_to_unbounded(self):
        svc = self._service_with({'rounds': {'2': {'scheduled_at': 'not-a-date'}}})
        assert await svc._round_window(7, EASTERN_TZ) == (None, None)


# ---------------------------------------------------------------------------
# _best_candidate
# ---------------------------------------------------------------------------


class TestBestCandidate:
    def test_returns_none_when_no_candidates(self, service):
        result = service._best_candidate([], [], {}, [], timedelta(hours=2))
        assert result is None

    def test_takes_the_earliest_slot_with_room(self, service):
        duration = timedelta(hours=2)
        candidates = [(_local(h), _local(h) + duration) for h in (10, 12, 14)]
        match = make_match(scheduled_hour=10, player_count=4)
        result = service._best_candidate(candidates, [1, 2], {}, [match], duration, capacity=40)
        assert result.astimezone(EASTERN_TZ).hour == 10

    def test_keeps_headroom_before_filling_to_the_cap(self, service):
        # 28 players on the floor at 10:00. Two more is 30, at the 75% line of
        # a 40 cap, so 10:00 is fine; 30 already there pushes to the free 12:00.
        duration = timedelta(hours=2)
        candidates = [(_local(10), _local(12)), (_local(12), _local(14))]
        at_line = [make_match(10, player_count=28)]
        over_line = [make_match(10, player_count=30)]
        assert service._best_candidate(
            candidates, [1, 2], {}, at_line, duration, capacity=40,
        ).astimezone(EASTERN_TZ).hour == 10
        assert service._best_candidate(
            candidates, [1, 2], {}, over_line, duration, capacity=40,
        ).astimezone(EASTERN_TZ).hour == 12

    def test_fills_past_headroom_when_nothing_else_fits(self, service):
        duration = timedelta(hours=2)
        candidates = [(_local(10), _local(12)), (_local(12), _local(14))]
        busy = [make_match(10, player_count=34), make_match(12, player_count=36)]
        result = service._best_candidate(candidates, [1, 2], {}, busy, duration, capacity=40)
        assert result.astimezone(EASTERN_TZ).hour == 10

    def test_never_goes_past_the_cap(self, service):
        duration = timedelta(hours=2)
        full = [make_match(10, player_count=39)]
        result = service._best_candidate(
            [(_local(10), _local(12))], [1, 2], {}, full, duration, capacity=40,
        )
        assert result is None

    def test_never_double_books_a_player(self, service):
        duration = timedelta(minutes=90)
        candidates = [(_local(10), _local(10) + duration), (_local(13), _local(13) + duration)]
        other_match = make_match(11, user_ids=[1, 99])
        result = service._best_candidate(candidates, [1, 2], {}, [other_match], duration)
        assert result.astimezone(EASTERN_TZ).hour == 13

    def test_a_players_back_to_back_match_is_not_a_clash(self, service):
        duration = timedelta(minutes=90)
        before = make_match(8, user_ids=[1, 99], tournament_duration=120)
        result = service._best_candidate(
            [(_local(10), _local(10) + duration)], [1, 2], {}, [before], duration,
        )
        assert result is not None

    def test_preferred_beats_earlier_slot_only_on_the_same_day(self, service):
        from models import VolunteerAvailabilityStatus

        duration = timedelta(hours=2)
        avail_map = {1: [SimpleNamespace(
            starts_at=_local(14, 16), ends_at=_local(18, 16),
            status=VolunteerAvailabilityStatus.PREFERRED,
        )]}
        today = (_local(10), _local(12))
        tomorrow_preferred = (_local(14, 16), _local(16, 16))
        result = service._best_candidate([today, tomorrow_preferred], [1], avail_map, [], duration)
        assert result.astimezone(EASTERN_TZ).day == 15

    def test_skips_slots_a_player_blocked_out(self, service):
        from models import VolunteerAvailabilityStatus

        slot_start = _local(10)
        slot_end = slot_start + timedelta(hours=2)
        player_id = 1

        unavailable_window = SimpleNamespace(
            starts_at=_local(8),
            ends_at=_local(14),
            status=VolunteerAvailabilityStatus.UNAVAILABLE,
        )
        avail_map = {player_id: [unavailable_window]}

        result = service._best_candidate(
            [(slot_start, slot_end)],
            [player_id],
            avail_map,
            [],
            timedelta(hours=2),
        )
        assert result is None

    def test_slot_outside_every_window_is_eligible(self, service):
        """Opt-out: a player who blocked a morning is still schedulable at noon."""
        from models import VolunteerAvailabilityStatus

        slot_start = _local(12)
        player_id = 1
        avail_map = {player_id: [SimpleNamespace(
            starts_at=_local(8),
            ends_at=_local(11),
            status=VolunteerAvailabilityStatus.UNAVAILABLE,
        )]}

        result = service._best_candidate(
            [(slot_start, slot_start + timedelta(hours=2))],
            [player_id],
            avail_map,
            [],
            timedelta(hours=2),
        )
        assert result is not None
        assert result.astimezone(EASTERN_TZ).hour == 12

    def test_prefers_a_slot_a_player_marked_preferred(self, service):
        from models import VolunteerAvailabilityStatus

        duration = timedelta(hours=2)
        plain, preferred = _local(10), _local(14)
        player_id = 1
        avail_map = {player_id: [SimpleNamespace(
            starts_at=_local(14),
            ends_at=_local(18),
            status=VolunteerAvailabilityStatus.PREFERRED,
        )]}

        result = service._best_candidate(
            [(plain, plain + duration), (preferred, preferred + duration)],
            [player_id],
            avail_map,
            [],
            duration,
        )
        assert result is not None
        assert result.astimezone(EASTERN_TZ).hour == 14


class TestInStageHours:
    def test_keeps_only_slots_inside_daily_hours(self):
        from datetime import time

        from application.services.round_windows import RoundWindow

        window = RoundWindow(daily_from=time(18), daily_until=time(23))
        hour = timedelta(hours=1)
        slots = [(_local(h), _local(h) + hour) for h in (12, 18, 22, 23)]
        kept = MatchSuggestionService._in_stage_hours(slots, window, hour, EASTERN_TZ)
        assert [s.hour for s, _ in kept] == [18, 22]

    def test_no_window_keeps_everything(self):
        slots = [(_local(12), _local(13))]
        assert MatchSuggestionService._in_stage_hours(slots, None, timedelta(hours=1), EASTERN_TZ) == slots
