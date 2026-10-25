"""Coverage tests for MatchDisplayService and MatchSuggestionService.

The existing unit tests exercise the pure formatting/scoring helpers with
``SimpleNamespace`` fakes; this file drives the DB-backed read/suggestion paths
(repository fetches, filters, event-window fallback) with real ORM rows.
"""

import json
from datetime import datetime, timedelta, timezone

import pytest

from application.services.match.match_display_service import NO_STAGE, MatchDisplayService
from application.services.match.match_suggestion_service import MatchSuggestionService, _covers
from application.utils.timezone import EASTERN_TZ, format_local_datetime, now_local
from models import (
    Commentator,
    GeneratedSeeds,
    Match,
    MatchAcknowledgment,
    MatchPlayers,
    PlayerAvailability,
    Stage,
    SystemConfiguration,
    Tournament,
    Tracker,
    User,
    VolunteerAvailabilityStatus,
)

UTC = timezone.utc
Status = VolunteerAvailabilityStatus


from tests.factories import utc


def to_display(y, mo, d, h=0, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=EASTERN_TZ)


async def _user(discord_id, name="U"):
    return await User.create(discord_id=discord_id, username=name, display_name=name)


# ===========================================================================
# MatchDisplayService
# ===========================================================================


class TestGetMatchForDisplay:
    async def test_returns_none_for_missing_match(self, db):
        svc = MatchDisplayService()
        assert await svc.get_match_for_display(99999) is None

    async def test_returns_formatted_dict_with_all_relations(self, db):
        t = await Tournament.create(name="Champs", seed_generator="alttpr")
        stage = await Stage.create(name="Stage A", stream_url="https://twitch.tv/sgl")
        seed = await GeneratedSeeds.create(seed_url="https://alttpr.com/h/abc")
        p1 = await _user(2001, "Alice")
        p2 = await _user(2002, "Bob")
        comm = await _user(2003, "Cara")
        trk = await _user(2004, "Dan")
        match = await Match.create(
            tournament=t, stage=stage, generated_seed=seed,
            scheduled_at=utc(2025, 1, 15, 20, 0), is_stream_candidate=True,
        )
        await MatchPlayers.create(match=match, user=p1, finish_rank=1, assigned_station="A")
        await MatchPlayers.create(match=match, user=p2, finish_rank=2, assigned_station="B")
        await Commentator.create(match=match, user=comm, approved=True, acknowledged_at=utc(2025, 1, 15, 19, 0))
        await Tracker.create(match=match, user=trk, approved=False)
        await MatchAcknowledgment.create(
            match=match, user=p1, acknowledged_at=utc(2025, 1, 15, 19, 30), auto_acknowledged=True,
        )

        svc = MatchDisplayService()
        result = await svc.get_match_for_display(match.id)

        assert result["id"] == match.id
        assert result["tournament"] == "Champs"
        assert result["is_stream_candidate"] is True
        assert result["stage"] == "Stage A"
        assert result["stage_url"] == "https://twitch.tv/sgl"
        assert result["seed"] == "https://alttpr.com/h/abc"
        assert result["tournament_seed_generator"] == "alttpr"
        assert {p["name"] for p in result["players"]} == {"Alice", "Bob"}
        assert len(result["commentators"]) == 1 and result["commentators"][0]["approved"] is True
        assert result["commentators"][0]["acknowledged"] is True
        assert len(result["trackers"]) == 1 and result["trackers"][0]["approved"] is False
        acked = [a for a in result["acknowledgments"] if a["acknowledged"]]
        assert len(acked) == 1 and acked[0]["auto"] is True and acked[0]["ts"]


class TestFormatStateBranches:
    async def test_finished_state_uses_finished_timestamp(self, db):
        t = await Tournament.create(name="T")
        match = await Match.create(
            tournament=t, scheduled_at=utc(2025, 1, 15, 20, 0), finished_at=utc(2025, 1, 15, 21, 0),
        )
        svc = MatchDisplayService()
        result = await svc.get_match_for_display(match.id)
        assert result["state"] == "Finished"
        assert result["state_timestamp"] == format_local_datetime(utc(2025, 1, 15, 21, 0))

    async def test_started_state_uses_started_timestamp(self, db):
        t = await Tournament.create(name="T")
        match = await Match.create(
            tournament=t, scheduled_at=utc(2025, 1, 15, 20, 0), started_at=utc(2025, 1, 15, 20, 30),
        )
        svc = MatchDisplayService()
        result = await svc.get_match_for_display(match.id)
        assert result["state"] == "Started"
        assert result["state_timestamp"] == format_local_datetime(utc(2025, 1, 15, 20, 30))

    async def test_checked_in_state_uses_seated_timestamp(self, db):
        t = await Tournament.create(name="T")
        match = await Match.create(
            tournament=t, scheduled_at=utc(2025, 1, 15, 20, 0), seated_at=utc(2025, 1, 15, 19, 45),
        )
        svc = MatchDisplayService()
        result = await svc.get_match_for_display(match.id)
        assert result["state"] == "Checked In"
        assert result["state_timestamp"] == format_local_datetime(utc(2025, 1, 15, 19, 45))

    async def test_confirmed_state_uses_confirmed_timestamp(self, db):
        t = await Tournament.create(name="T")
        match = await Match.create(
            tournament=t, scheduled_at=utc(2025, 1, 15, 20, 0),
            seated_at=utc(2025, 1, 15, 19, 45), started_at=utc(2025, 1, 15, 20, 30),
            finished_at=utc(2025, 1, 15, 21, 0), confirmed_at=utc(2025, 1, 15, 21, 15),
        )
        svc = MatchDisplayService()
        result = await svc.get_match_for_display(match.id)
        assert result["state"] == "Confirmed"
        assert result["state_timestamp"] == format_local_datetime(utc(2025, 1, 15, 21, 15))


class TestGetMatchesForDisplay:
    async def test_returns_all_without_filters(self, db):
        t = await Tournament.create(name="T")
        await Match.create(tournament=t, scheduled_at=utc(2025, 1, 15, 20, 0))
        await Match.create(tournament=t, scheduled_at=utc(2025, 1, 15, 21, 0))
        svc = MatchDisplayService()
        rows = await svc.get_matches_for_display()
        assert len(rows) == 2

    async def test_filters_by_tournament(self, db):
        t1 = await Tournament.create(name="T1")
        t2 = await Tournament.create(name="T2")
        await Match.create(tournament=t1, scheduled_at=utc(2025, 1, 15, 20, 0))
        await Match.create(tournament=t2, scheduled_at=utc(2025, 1, 15, 20, 0))
        svc = MatchDisplayService()
        rows = await svc.get_matches_for_display(tournament_ids=[t1.id])
        assert len(rows) == 1 and rows[0]["tournament"] == "T1"

    async def test_filters_by_stage(self, db):
        t = await Tournament.create(name="T")
        stage = await Stage.create(name="Stage A")
        await Match.create(tournament=t, stage=stage, scheduled_at=utc(2025, 1, 15, 20, 0))
        await Match.create(tournament=t, scheduled_at=utc(2025, 1, 15, 20, 0))
        svc = MatchDisplayService()
        rows = await svc.get_matches_for_display(stage_ids=[stage.id])
        assert len(rows) == 1 and rows[0]["stage"] == "Stage A"

    async def test_no_stage_keeps_only_unstaged(self, db):
        t = await Tournament.create(name="T")
        stage = await Stage.create(name="Stage A")
        await Match.create(tournament=t, stage=stage, scheduled_at=utc(2025, 1, 15, 20, 0))
        unstaged = await Match.create(tournament=t, scheduled_at=utc(2025, 1, 15, 20, 0))
        rows = await MatchDisplayService().get_matches_for_display(stage_ids=[NO_STAGE])
        assert [r["id"] for r in rows] == [unstaged.id]

    async def test_no_stage_mixes_with_a_stage(self, db):
        t = await Tournament.create(name="T")
        a = await Stage.create(name="Stage A")
        b = await Stage.create(name="Stage B")
        on_a = await Match.create(tournament=t, stage=a, scheduled_at=utc(2025, 1, 15, 20, 0))
        await Match.create(tournament=t, stage=b, scheduled_at=utc(2025, 1, 15, 20, 0))
        unstaged = await Match.create(tournament=t, scheduled_at=utc(2025, 1, 15, 20, 0))
        rows = await MatchDisplayService().get_matches_for_display(stage_ids=[NO_STAGE, a.id])
        assert {r["id"] for r in rows} == {on_a.id, unstaged.id}

    async def test_only_upcoming_excludes_finished(self, db):
        t = await Tournament.create(name="T")
        await Match.create(tournament=t, scheduled_at=utc(2025, 1, 15, 20, 0))
        await Match.create(
            tournament=t, scheduled_at=utc(2025, 1, 15, 21, 0), finished_at=utc(2025, 1, 15, 22, 0),
        )
        svc = MatchDisplayService()
        rows = await svc.get_matches_for_display(only_upcoming=True)
        assert len(rows) == 1

    async def test_filters_by_user_discord_id(self, db):
        t = await Tournament.create(name="T")
        p = await _user(3001, "Alice")
        m1 = await Match.create(tournament=t, scheduled_at=utc(2025, 1, 15, 20, 0))
        await MatchPlayers.create(match=m1, user=p)
        await Match.create(tournament=t, scheduled_at=utc(2025, 1, 15, 21, 0))
        svc = MatchDisplayService()
        rows = await svc.get_matches_for_display(user_discord_id=str(p.discord_id))
        assert len(rows) == 1 and rows[0]["id"] == m1.id

    async def test_acknowledgments_mapped_per_match(self, db):
        t = await Tournament.create(name="T")
        p = await _user(3101, "Alice")
        m = await Match.create(tournament=t, scheduled_at=utc(2025, 1, 15, 20, 0))
        await MatchPlayers.create(match=m, user=p)
        await MatchAcknowledgment.create(match=m, user=p, acknowledged_at=utc(2025, 1, 15, 19, 0))
        svc = MatchDisplayService()
        rows = await svc.get_matches_for_display()
        assert rows[0]["acknowledgments"][0]["acknowledged"] is True


class TestFilterDropdowns:
    async def test_tournaments_for_filter(self, db):
        t = await Tournament.create(name="T1")
        svc = MatchDisplayService()
        result = await svc.get_tournaments_for_filter()
        assert result.get(t.id) == "T1"

    async def test_stages_for_filter(self, db):
        stage = await Stage.create(name="Stage A")
        svc = MatchDisplayService()
        result = await svc.get_stages_for_filter()
        assert result.get(stage.id) == "Stage A"


# ===========================================================================
# MatchSuggestionService
# ===========================================================================


class TestCovers:
    def test_no_overlap_returns_available(self):
        """Opt-out: a slot no window reaches is one the player can be given."""
        w = _win(to_display(2026, 1, 15, 8), to_display(2026, 1, 15, 9), Status.UNAVAILABLE)
        assert _covers([w], to_display(2026, 1, 15, 10), to_display(2026, 1, 15, 12)) == Status.AVAILABLE

    def test_no_windows_at_all_returns_available(self):
        assert _covers([], to_display(2026, 1, 15, 10), to_display(2026, 1, 15, 12)) == Status.AVAILABLE

    def test_available_overlap_returns_available(self):
        w = _win(to_display(2026, 1, 15, 8), to_display(2026, 1, 15, 14), Status.AVAILABLE)
        assert _covers([w], to_display(2026, 1, 15, 10), to_display(2026, 1, 15, 12)) == Status.AVAILABLE

    def test_preferred_overlap_returns_preferred(self):
        w = _win(to_display(2026, 1, 15, 8), to_display(2026, 1, 15, 14), Status.PREFERRED)
        assert _covers([w], to_display(2026, 1, 15, 10), to_display(2026, 1, 15, 12)) == Status.PREFERRED

    def test_unavailable_overlap_wins(self):
        avail = _win(to_display(2026, 1, 15, 8), to_display(2026, 1, 15, 14), Status.AVAILABLE)
        unavail = _win(to_display(2026, 1, 15, 9), to_display(2026, 1, 15, 13), Status.UNAVAILABLE)
        assert _covers([avail, unavail], to_display(2026, 1, 15, 10), to_display(2026, 1, 15, 12)) == Status.UNAVAILABLE


class TestBookings:
    def test_skips_matches_without_scheduled_at(self):
        from types import SimpleNamespace

        match = SimpleNamespace(scheduled_at=None, players=[SimpleNamespace(user_id=1)], tournament=None)
        assert MatchSuggestionService._bookings([match], timedelta(hours=2)) == []

    def test_uses_the_tournament_length_and_player_ids(self):
        from types import SimpleNamespace

        match = SimpleNamespace(
            scheduled_at=to_display(2026, 1, 15, 10).astimezone(UTC),
            players=[SimpleNamespace(user_id=1), SimpleNamespace(user_id=2)],
            tournament=SimpleNamespace(average_match_duration=45, uses_tournament_room=True),
            stage_id=None,
        )
        [booking] = MatchSuggestionService._bookings([match], timedelta(minutes=90))
        assert booking.end - booking.start == timedelta(minutes=45)
        assert booking.player_count == 2
        assert booking.user_ids == frozenset({1, 2})


class TestGenerateCandidatesCeiling:
    def test_stops_at_to_dt_ceiling(self, service):
        from datetime import date

        from_dt = to_display(2026, 1, 15, 10)
        to_dt = to_display(2026, 1, 15, 12)
        candidates = service._generate_candidates(
            from_dt, to_dt, {}, timedelta(hours=1), date(2026, 1, 15), date(2026, 1, 15),
            EASTERN_TZ,
        )
        # 10:00, 10:30, 11:00, 11:30 — the 12:00 slot hits the to_dt break.
        assert len(candidates) == 4
        assert all(s < to_dt for s, _ in candidates)

    def test_uses_configured_day_window(self, service):
        from datetime import date, time

        hours_map = {date(2026, 1, 15): (time(10, 0), time(14, 0))}
        candidates = service._generate_candidates(
            to_display(2026, 1, 15, 10), to_display(2026, 1, 15, 14), hours_map,
            timedelta(hours=1), date(2026, 1, 15), date(2026, 1, 15), EASTERN_TZ,
        )
        assert len(candidates) > 0
        assert all(s.astimezone(EASTERN_TZ).hour >= 10 for s, _ in candidates)


class TestBestCandidate:
    def test_unconstrained_player_without_windows_is_eligible(self, service):
        slot = (to_display(2026, 1, 15, 10), to_display(2026, 1, 15, 12))
        result = service._best_candidate([slot], [99], {}, [], timedelta(hours=2))
        assert result is not None
        assert result.astimezone(EASTERN_TZ).hour == 10

    def test_slot_outside_a_players_windows_is_eligible(self, service):
        """Opt-out: blocking the early morning does not withdraw the rest."""
        w = _win(to_display(2026, 1, 15, 6), to_display(2026, 1, 15, 8), Status.UNAVAILABLE)
        slot = (to_display(2026, 1, 15, 10), to_display(2026, 1, 15, 12))
        result = service._best_candidate([slot], [1], {1: [w]}, [], timedelta(hours=2))
        assert result is not None
        assert result.astimezone(EASTERN_TZ).hour == 10

    def test_unavailable_window_disqualifies_slot(self, service):
        w = _win(to_display(2026, 1, 15, 8), to_display(2026, 1, 15, 14), Status.UNAVAILABLE)
        slot = (to_display(2026, 1, 15, 10), to_display(2026, 1, 15, 12))
        result = service._best_candidate([slot], [1], {1: [w]}, [], timedelta(hours=2))
        assert result is None

    def test_preferred_window_is_eligible(self, service):
        w = _win(to_display(2026, 1, 15, 8), to_display(2026, 1, 15, 14), Status.PREFERRED)
        slot = (to_display(2026, 1, 15, 10), to_display(2026, 1, 15, 12))
        result = service._best_candidate([slot], [1], {1: [w]}, [], timedelta(hours=2))
        assert result is not None
        assert result.astimezone(EASTERN_TZ).hour == 10


class TestBuildAvailabilityMap:
    async def test_groups_multiple_windows_per_user(self, db):
        user = await _user(4001, "Ann")
        await PlayerAvailability.create(
            user=user, starts_at=utc(2030, 1, 1, 10), ends_at=utc(2030, 1, 1, 12), status=Status.AVAILABLE,
        )
        await PlayerAvailability.create(
            user=user, starts_at=utc(2030, 1, 1, 14), ends_at=utc(2030, 1, 1, 16), status=Status.PREFERRED,
        )
        svc = MatchSuggestionService()
        result = await svc._build_availability_map([user.id], utc(2030, 1, 1, 0), utc(2030, 1, 2, 0))
        assert user.id in result
        assert len(result[user.id]) == 2


class TestSuggestMatchTime:
    async def test_returns_primary_slot(self, db):
        t = await Tournament.create(name="T", average_match_duration=60)
        today = now_local().date()
        await _event_window(today - timedelta(days=1), today + timedelta(days=2))
        svc = MatchSuggestionService()
        result = await svc.suggest_match_time(t.id, [])
        assert isinstance(result, datetime)
        assert result.utcoffset() == timedelta(0)

    async def test_falls_back_to_full_event_window(self, db):
        t = await Tournament.create(name="T", average_match_duration=90)
        today = now_local().date()
        start = today + timedelta(days=10)
        end = today + timedelta(days=12)
        await _event_window(start, end)
        svc = MatchSuggestionService()
        result = await svc.suggest_match_time(t.id, [])
        assert isinstance(result, datetime)
        assert result.astimezone(EASTERN_TZ).date() >= start

    async def test_raises_when_no_configured_day_is_open(self, db):
        t = await Tournament.create(name="T")
        today = now_local().date()
        start = today + timedelta(days=10)
        end = today + timedelta(days=12)
        await _event_window(start, end)
        # Configure hours only for a date outside the event window: every event
        # day is then unconfigured and skipped, leaving no eligible slot.
        unrelated = (today + timedelta(days=1)).isoformat()
        await SystemConfiguration.create(
            name="tournament_hours_by_date",
            value=json.dumps({unrelated: {"open": "10:00", "close": "14:00"}}),
        )
        svc = MatchSuggestionService()
        with pytest.raises(ValueError):
            await svc.suggest_match_time(t.id, [])


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _win(starts_at, ends_at, status):
    from types import SimpleNamespace

    return SimpleNamespace(starts_at=starts_at, ends_at=ends_at, status=status)


async def _event_window(start, end):
    await SystemConfiguration.create(name="event_start_date", value=start.isoformat())
    await SystemConfiguration.create(name="event_end_date", value=end.isoformat())


@pytest.fixture
def service():
    return MatchSuggestionService()
