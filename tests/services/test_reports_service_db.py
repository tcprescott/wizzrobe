"""DB-backed tests for the async report methods of ``ReportsService``.

The pure helpers (_local, _match_window, _auto_interval_minutes, peak_times,
event_day_bounds) are covered in ``test_reports_service.py``. This module
exercises the five async methods that query the ``db`` fixture:
``generate_capacity_forecast``, ``matches_active_at``, ``match_operations``,
``crew_coverage`` and ``stage_utilization``.
"""

from datetime import datetime, timezone

import pytest

from application.services.reports_service import ReportsService
from application.utils.timezone import EASTERN_TZ
from models import (
    Commentator,
    Match,
    MatchPlayers,
    Stage,
    SystemConfiguration,
    Tournament,
    Tracker,
    User,
)

UTC = timezone.utc


from tests.api_helpers import create_community_member
from tests.factories import utc


async def _actor():
    """Reports gate their reader; these tests read as the system actor."""
    from application.services.user_service import UserService
    return await UserService().get_system_user()


def to_display(y, mo, d, h=0, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=EASTERN_TZ)


async def _user(discord_id, name):
    return await User.create(discord_id=discord_id, username=name, display_name=name)


# A three-hour Eastern window on 2025-10-23 (EDT, UTC-4): 13:00 -> 16:00.
FORECAST_START = to_display(2025, 10, 23, 13, 0)
FORECAST_END = to_display(2025, 10, 23, 16, 0)

# A full-day Eastern window used for operations / crew / utilization reports.
DAY_START = to_display(2025, 10, 23, 0, 0)
DAY_END = to_display(2025, 10, 24, 0, 0)


# ---------------------------------------------------------------------------
# generate_capacity_forecast
# ---------------------------------------------------------------------------


class TestGenerateCapacityForecast:
    async def test_concurrent_counts_and_shape(self, db):
        """Two overlapping matches: one placed on stream with explicit players,
        one unplaced relying on the players_per_match fallback."""
        t = await Tournament.create(name='Cup', players_per_match=2, average_match_duration=90)
        stage = await Stage.create(name='Alpha')
        u1 = await _user(1, 'Alice')
        u2 = await _user(2, 'Bob')

        # Match A: 14:00 ET scheduled, window [13:00, 15:30], on stream, 2 players.
        m_a = await Match.create(tournament=t, stage=stage, scheduled_at=utc(2025, 10, 23, 18, 0))
        await MatchPlayers.create(match=m_a, user=u1)
        await MatchPlayers.create(match=m_a, user=u2)

        # Match B: 15:00 ET scheduled, window [14:00, 16:30], no stage, no players
        # -> player_count falls back to tournament.players_per_match == 2.
        m_b = await Match.create(tournament=t, scheduled_at=utc(2025, 10, 23, 19, 0))

        out = await ReportsService().generate_capacity_forecast(FORECAST_START, FORECAST_END, actor=(await _actor()))

        assert out['interval_minutes'] == 15
        assert out['start'] == FORECAST_START
        assert out['end'] == FORECAST_END
        assert out['max_capacity'] == 60  # SystemConfiguration default

        n = len(out['intervals'])
        assert n == 13  # 13:00..16:00 inclusive at 15-minute steps
        assert len(out['player_counts']) == n
        assert len(out['on_stream_player_counts']) == n
        assert len(out['match_ids_per_interval']) == n

        # At 13:00 (index 0) only A is live: 2 players, both on stream.
        assert out['player_counts'][0] == 2
        assert out['on_stream_player_counts'][0] == 2
        assert out['match_ids_per_interval'][0] == [m_a.id]

        # Peak concurrency is A + B overlapping = 4; only A's 2 are ever on stream.
        assert max(out['player_counts']) == 4
        assert max(out['on_stream_player_counts']) == 2
        # Both match ids appear at the overlap.
        assert any(set(ids) == {m_a.id, m_b.id} for ids in out['match_ids_per_interval'])

    async def test_end_before_start_collapses_to_single_interval(self, db):
        out = await ReportsService().generate_capacity_forecast(FORECAST_END, FORECAST_START, actor=(await _actor()))
        assert out['end'] == out['start'] == FORECAST_END
        assert len(out['intervals']) == 1
        assert out['player_counts'] == [0]

    async def test_window_outside_range_is_skipped(self, db):
        """A match whose active window ends before the forecast start contributes
        nothing even though it is inside the +/-24h prefetch band."""
        t = await Tournament.create(name='Cup', players_per_match=2, average_match_duration=90)
        # 10:00 ET scheduled -> window [09:00, 11:30], entirely before 13:00.
        await Match.create(tournament=t, scheduled_at=utc(2025, 10, 23, 14, 0))

        out = await ReportsService().generate_capacity_forecast(FORECAST_START, FORECAST_END, actor=(await _actor()))
        assert all(c == 0 for c in out['player_counts'])
        assert all(ids == [] for ids in out['match_ids_per_interval'])

    async def test_max_capacity_reads_system_config(self, db):
        await SystemConfiguration.create(name='max_concurrent_players', value='100')
        out = await ReportsService().generate_capacity_forecast(FORECAST_START, FORECAST_END, actor=(await _actor()))
        assert out['max_capacity'] == 100

    async def test_tournament_filter(self, db):
        t1 = await Tournament.create(name='A', players_per_match=2, average_match_duration=90)
        t2 = await Tournament.create(name='B', players_per_match=2, average_match_duration=90)
        await Match.create(tournament=t1, scheduled_at=utc(2025, 10, 23, 18, 0))
        await Match.create(tournament=t2, scheduled_at=utc(2025, 10, 23, 18, 0))

        out = await ReportsService().generate_capacity_forecast(
            FORECAST_START, FORECAST_END, tournament_id=t1.id, actor=(await _actor()),
        )
        # Only t1's match (2 players via fallback) is ever counted.
        assert max(out['player_counts']) == 2

    async def test_peak_times_over_forecast_output(self, db):
        t = await Tournament.create(name='Cup', players_per_match=2, average_match_duration=90)
        await Match.create(tournament=t, scheduled_at=utc(2025, 10, 23, 18, 0))
        out = await ReportsService().generate_capacity_forecast(FORECAST_START, FORECAST_END, actor=(await _actor()))
        peaks = ReportsService.peak_times(out['intervals'], out['player_counts'], top_n=3)
        assert len(peaks) == 3
        assert peaks[0][1] == max(out['player_counts']) == 2


# ---------------------------------------------------------------------------
# matches_active_at
# ---------------------------------------------------------------------------


class TestMatchesActiveAt:
    async def test_returns_only_matches_live_at_instant(self, db):
        t = await Tournament.create(name='Cup', average_match_duration=90)
        # A: window [13:00, 15:30] -> live at 14:00.
        m_a = await Match.create(tournament=t, scheduled_at=utc(2025, 10, 23, 18, 0))
        # B: window [17:00, 19:30] -> not live at 14:00.
        await Match.create(tournament=t, scheduled_at=utc(2025, 10, 23, 22, 0))

        active = await ReportsService().matches_active_at(to_display(2025, 10, 23, 14, 0), actor=(await _actor()))
        assert [m.id for m in active] == [m_a.id]

    async def test_tournament_filter(self, db):
        t1 = await Tournament.create(name='A', average_match_duration=90)
        t2 = await Tournament.create(name='B', average_match_duration=90)
        m1 = await Match.create(tournament=t1, scheduled_at=utc(2025, 10, 23, 18, 0))
        await Match.create(tournament=t2, scheduled_at=utc(2025, 10, 23, 18, 0))

        active = await ReportsService().matches_active_at(
            to_display(2025, 10, 23, 14, 0), tournament_id=t1.id, actor=(await _actor()),
        )
        assert [m.id for m in active] == [m1.id]

    async def test_empty_when_nothing_live(self, db):
        t = await Tournament.create(name='Cup', average_match_duration=90)
        await Match.create(tournament=t, scheduled_at=utc(2025, 10, 23, 18, 0))
        # 20:00 ET is after A's window end (15:30) but still inside the prefetch band.
        active = await ReportsService().matches_active_at(to_display(2025, 10, 23, 20, 0), actor=(await _actor()))
        assert active == []


# ---------------------------------------------------------------------------
# match_operations
# ---------------------------------------------------------------------------


class TestMatchOperations:
    async def _seed(self):
        t = await Tournament.create(name='Cup', average_match_duration=90)
        stage = await Stage.create(name='Alpha')
        u1 = await _user(1, 'Alice')

        # M1: 3 min late (on time), 90 min long, confirmed 7 min after finish, on stream.
        m1 = await Match.create(
            tournament=t, stage=stage,
            scheduled_at=utc(2025, 10, 23, 18, 0),
            started_at=utc(2025, 10, 23, 18, 3),
            finished_at=utc(2025, 10, 23, 19, 33),
            confirmed_at=utc(2025, 10, 23, 19, 40),
        )
        await MatchPlayers.create(match=m1, user=u1)

        # M2: 20 min late (not on time), 90 min long, never confirmed.
        m2 = await Match.create(
            tournament=t,
            scheduled_at=utc(2025, 10, 23, 20, 0),
            started_at=utc(2025, 10, 23, 20, 20),
            finished_at=utc(2025, 10, 23, 21, 50),
        )

        # M3: never started -> no delay/duration/lag.
        m3 = await Match.create(tournament=t, scheduled_at=utc(2025, 10, 23, 22, 0))

        # M4 in a second tournament, never started -> exercises the None aggregate branch.
        t2 = await Tournament.create(name='Other', average_match_duration=None)
        m4 = await Match.create(tournament=t2, scheduled_at=utc(2025, 10, 23, 16, 0))
        return t, t2, stage, m1, m2, m3, m4

    async def test_rows_capture_delay_duration_and_lag(self, db):
        t, t2, stage, m1, m2, m3, m4 = await self._seed()
        out = await ReportsService().match_operations(DAY_START, DAY_END, actor=(await _actor()))
        rows = {r['match_id']: r for r in out['rows']}
        assert len(rows) == 4

        r1 = rows[m1.id]
        assert r1['start_delay_min'] == 3
        assert r1['duration_min'] == 90
        assert r1['confirmation_lag_min'] == 7
        assert r1['state'] == 'Finished'
        assert r1['stage'] == stage.name
        assert r1['tournament_name'] == t.name
        assert r1['player_count'] == 1

        r2 = rows[m2.id]
        assert r2['start_delay_min'] == 20
        assert r2['duration_min'] == 90
        assert r2['confirmation_lag_min'] is None

        r3 = rows[m3.id]
        assert r3['start_delay_min'] is None
        assert r3['duration_min'] is None
        assert r3['state'] == 'Scheduled'
        assert r3['stage'] == ''

    async def test_per_tournament_aggregates(self, db):
        t, t2, *_ = await self._seed()
        out = await ReportsService().match_operations(DAY_START, DAY_END, actor=(await _actor()))
        aggs = {a['tournament_id']: a for a in out['aggregates']}

        a = aggs[t.id]
        assert a['matches_total'] == 3
        assert a['matches_started'] == 2
        assert a['matches_finished'] == 2
        assert a['avg_start_delay_min'] == 11.5  # (3 + 20) / 2
        assert a['avg_duration_min'] == 90.0
        assert a['on_time_pct'] == 50.0  # only M1 within +/-5 min
        assert a['expected_avg_min'] == 90

        # Second tournament has one never-started match -> all rate metrics None.
        b = aggs[t2.id]
        assert b['matches_total'] == 1
        assert b['matches_started'] == 0
        assert b['matches_finished'] == 0
        assert b['avg_start_delay_min'] is None
        assert b['avg_duration_min'] is None
        assert b['on_time_pct'] is None
        assert b['expected_avg_min'] is None

    async def test_tournament_filter(self, db):
        t, t2, *_ = await self._seed()
        out = await ReportsService().match_operations(DAY_START, DAY_END, tournament_id=t.id, actor=(await _actor()))
        assert {r['tournament_id'] for r in out['rows']} == {t.id}
        assert [a['tournament_id'] for a in out['aggregates']] == [t.id]


# ---------------------------------------------------------------------------
# crew_coverage
# ---------------------------------------------------------------------------


    async def test_counts_a_timestamp_without_its_pair(self, db):
        # Insights counts started/finished on the timestamp alone; Reports agrees,
        # so a result recorded without a start still counts as finished.
        t = await Tournament.create(name='Solo')
        await Match.create(tournament=t, scheduled_at=utc(2025, 10, 23, 16, 0),
                           finished_at=utc(2025, 10, 23, 17, 0))
        out = await ReportsService().match_operations(DAY_START, DAY_END, actor=(await _actor()))
        agg = next(a for a in out['aggregates'] if a['tournament_id'] == t.id)
        assert agg['matches_started'] == 0
        assert agg['matches_finished'] == 1
        assert agg['avg_duration_min'] is None


class TestCrewCoverage:
    async def _seed(self):
        # avg 60 -> each match window is exactly 2.0 hours (sched-1h .. sched+60m).
        t = await Tournament.create(name='Cup', average_match_duration=60)
        u_alice = await _user(1, 'Alice')
        u_bob = await _user(2, 'Bob')
        u_cara = await _user(3, 'Cara')

        # M1: stream candidate, fully covered (approved commentator + tracker).
        m1 = await Match.create(
            tournament=t, scheduled_at=utc(2025, 10, 23, 18, 0), is_stream_candidate=True,
        )
        await Commentator.create(user=u_alice, match=m1, approved=True)
        await Tracker.create(user=u_bob, match=m1, approved=True)

        # M2: stream candidate, tracker NOT approved -> coverage gap.
        m2 = await Match.create(
            tournament=t, scheduled_at=utc(2025, 10, 23, 20, 0), is_stream_candidate=True,
        )
        await Commentator.create(user=u_alice, match=m2, approved=True)
        await Tracker.create(user=u_bob, match=m2, approved=False)

        # M3: not a stream candidate -> never flagged even if uncovered.
        m3 = await Match.create(
            tournament=t, scheduled_at=utc(2025, 10, 23, 22, 0), is_stream_candidate=False,
        )
        await Commentator.create(user=u_cara, match=m3, approved=True)
        return t, u_alice, u_bob, u_cara, m1, m2, m3

    async def test_coverage_rows_and_gap_flag(self, db):
        t, alice, bob, cara, m1, m2, m3 = await self._seed()
        out = await ReportsService().crew_coverage(DAY_START, DAY_END, actor=(await _actor()))
        rows = {r['match_id']: r for r in out['coverage_rows']}
        assert len(rows) == 3

        r1 = rows[m1.id]
        assert r1['is_stream_candidate'] is True
        assert r1['commentators_approved'] == 1
        assert r1['commentators_total'] == 1
        assert r1['trackers_approved'] == 1
        assert r1['trackers_total'] == 1
        assert r1['coverage_gap'] is False

        r2 = rows[m2.id]
        assert r2['trackers_approved'] == 0
        assert r2['trackers_total'] == 1
        assert r2['coverage_gap'] is True

        r3 = rows[m3.id]
        assert r3['is_stream_candidate'] is False
        assert r3['coverage_gap'] is False

    async def test_contribution_rows_hours_and_order(self, db):
        t, alice, bob, cara, *_ = await self._seed()
        out = await ReportsService().crew_coverage(DAY_START, DAY_END, actor=(await _actor()))
        contrib = {c['user_id']: c for c in out['contribution_rows']}

        a = contrib[alice.id]
        assert a['name'] == 'Alice'
        assert a['commentator_approved'] == 2
        assert a['commentator_total'] == 2
        assert a['hours_covered'] == 4.0
        assert a['hours_total'] == 4.0

        b = contrib[bob.id]
        assert b['tracker_approved'] == 1
        assert b['tracker_total'] == 2
        assert b['hours_covered'] == 2.0  # only the approved signup contributes
        assert b['hours_total'] == 4.0

        # Sorted by -hours_covered then name: Alice (4.0), Bob (2.0), Cara (2.0).
        assert [c['name'] for c in out['contribution_rows']] == ['Alice', 'Bob', 'Cara']

    async def test_user_id_filter(self, db):
        t, alice, bob, cara, m1, m2, m3 = await self._seed()
        out = await ReportsService().crew_coverage(DAY_START, DAY_END, user_id=alice.id, actor=(await _actor()))
        # coverage_rows only include matches Alice touched (M1, M2 -- not M3).
        assert {r['match_id'] for r in out['coverage_rows']} == {m1.id, m2.id}
        # contribution_rows are limited to Alice.
        assert [c['user_id'] for c in out['contribution_rows']] == [alice.id]

    async def test_approved_only_excludes_uncovered_hours_from_total(self, db):
        t, alice, bob, cara, *_ = await self._seed()
        out = await ReportsService().crew_coverage(DAY_START, DAY_END, approved_only=True, actor=(await _actor()))
        contrib = {c['user_id']: c for c in out['contribution_rows']}

        a = contrib[alice.id]
        assert a['hours_covered'] == 4.0
        assert a['hours_total'] == 0.0  # approved_only suppresses hours_total accumulation

        b = contrib[bob.id]
        assert b['hours_covered'] == 2.0
        assert b['hours_total'] == 0.0
        assert b['tracker_total'] == 2  # totals still count both signups

    async def test_tournament_filter(self, db):
        t1 = await Tournament.create(name='A', average_match_duration=60)
        t2 = await Tournament.create(name='B', average_match_duration=60)
        u = await _user(1, 'Alice')
        m1 = await Match.create(tournament=t1, scheduled_at=utc(2025, 10, 23, 18, 0))
        m2 = await Match.create(tournament=t2, scheduled_at=utc(2025, 10, 23, 18, 0))
        await Commentator.create(user=u, match=m1, approved=True)
        await Commentator.create(user=u, match=m2, approved=True)

        out = await ReportsService().crew_coverage(DAY_START, DAY_END, tournament_id=t1.id, actor=(await _actor()))
        assert {r['match_id'] for r in out['coverage_rows']} == {m1.id}
        assert contrib_hours(out, u.id) == 2.0  # only the t1 signup

    async def test_tournament_that_uses_no_trackers_has_no_gap(self, db):
        # The reports used to assume every streamed match needs a tracker, which
        # gave a commentary-only tournament a permanent false gap.
        t = await Tournament.create(
            name='Comms only', average_match_duration=60,
            required_commentators=1, required_trackers=0,
        )
        alice = await _user(1, 'Alice')
        match = await Match.create(
            tournament=t, scheduled_at=utc(2025, 10, 23, 18, 0), is_stream_candidate=True,
        )
        await Commentator.create(user=alice, match=match, approved=True)

        row = (await ReportsService().crew_coverage(DAY_START, DAY_END, actor=(await _actor())))['coverage_rows'][0]
        assert row['trackers_approved'] == 0
        assert row['trackers_required'] == 0
        assert row['commentators_required'] == 1
        assert row['coverage_gap'] is False

    async def test_requirement_above_one_is_enforced(self, db):
        t = await Tournament.create(
            name='Two comms', average_match_duration=60,
            required_commentators=2, required_trackers=0,
        )
        alice = await _user(1, 'Alice')
        bob = await _user(2, 'Bob')
        one = await Match.create(
            tournament=t, scheduled_at=utc(2025, 10, 23, 18, 0), is_stream_candidate=True,
        )
        two = await Match.create(
            tournament=t, scheduled_at=utc(2025, 10, 23, 20, 0), is_stream_candidate=True,
        )
        await Commentator.create(user=alice, match=one, approved=True)
        await Commentator.create(user=alice, match=two, approved=True)
        await Commentator.create(user=bob, match=two, approved=True)

        rows = {r['match_id']: r for r in
                (await ReportsService().crew_coverage(DAY_START, DAY_END, actor=(await _actor())))['coverage_rows']}
        assert rows[one.id]['coverage_gap'] is True   # one approved, two needed
        assert rows[two.id]['coverage_gap'] is False

    async def test_requiring_no_crew_at_all_covers_a_bare_candidate(self, db):
        t = await Tournament.create(
            name='Unstaffed', average_match_duration=60,
            required_commentators=0, required_trackers=0,
        )
        match = await Match.create(
            tournament=t, scheduled_at=utc(2025, 10, 23, 18, 0), is_stream_candidate=True,
        )
        row = (await ReportsService().crew_coverage(DAY_START, DAY_END, actor=(await _actor())))['coverage_rows'][0]
        assert row['match_id'] == match.id
        assert row['coverage_gap'] is False


def contrib_hours(out, user_id):
    for c in out['contribution_rows']:
        if c['user_id'] == user_id:
            return c['hours_covered']
    return None


# ---------------------------------------------------------------------------
# stage_utilization
# ---------------------------------------------------------------------------


class TestStageUtilization:
    async def _seed(self):
        t = await Tournament.create(name='Cup', average_match_duration=60)
        alpha = await Stage.create(name='Alpha')
        beta = await Stage.create(name='Beta')
        inactive = await Stage.create(name='Zulu', is_active=False)

        # Alpha: two back-to-back matches with a 5-minute gap.
        await Match.create(
            tournament=t, stage=alpha,
            scheduled_at=utc(2025, 10, 23, 18, 0),
            seated_at=utc(2025, 10, 23, 18, 0),   # 14:00 ET
            finished_at=utc(2025, 10, 23, 19, 0),  # 15:00 ET  -> 1.0h
        )
        await Match.create(
            tournament=t, stage=alpha,
            scheduled_at=utc(2025, 10, 23, 19, 5),
            seated_at=utc(2025, 10, 23, 19, 5),    # 15:05 ET (5 min after prev end)
            finished_at=utc(2025, 10, 23, 20, 5),  # 16:05 ET -> 1.0h
        )

        # Alpha: a match whose window collapses inside the clamp -> skipped.
        await Match.create(
            tournament=t, stage=alpha,
            scheduled_at=utc(2025, 10, 23, 4, 0),   # exactly the window start
            seated_at=utc(2025, 10, 23, 3, 0),      # before the window start
            finished_at=utc(2025, 10, 23, 3, 30),   # still before the window start
        )

        # Beta: a single 2-hour match, no neighbour.
        await Match.create(
            tournament=t, stage=beta,
            scheduled_at=utc(2025, 10, 23, 18, 0),
            seated_at=utc(2025, 10, 23, 18, 0),     # 14:00 ET
            finished_at=utc(2025, 10, 23, 20, 0),   # 16:00 ET -> 2.0h
        )

        # Unplaced stream candidate (no stage).
        await Match.create(
            tournament=t, scheduled_at=utc(2025, 10, 23, 18, 0), is_stream_candidate=True,
        )
        # Match in the inactive stage -> excluded from the stage set entirely.
        await Match.create(
            tournament=t, stage=inactive, scheduled_at=utc(2025, 10, 23, 18, 0),
        )
        return t, alpha, beta, inactive

    async def test_per_stage_hours_gaps_and_unplaced(self, db):
        t, alpha, beta, inactive = await self._seed()
        out = await ReportsService().stage_utilization(DAY_START, DAY_END, actor=(await _actor()))

        stages = {r['stage_id']: r for r in out['stages']}
        # Only active stages appear; the inactive stage is absent.
        assert set(stages) == {alpha.id, beta.id}

        a = stages[alpha.id]
        assert a['scheduled_hours'] == 2.0
        assert a['back_to_back_count'] == 1   # 5-minute gap < 15
        assert a['gap_hours'] == 0.1          # round(5/60, 1)
        assert len(a['matches']) == 2         # collapsed match was skipped

        b = stages[beta.id]
        assert b['scheduled_hours'] == 2.0
        assert b['back_to_back_count'] == 0
        assert b['gap_hours'] == 0.0
        assert len(b['matches']) == 1

        assert out['unplaced_candidate_count'] == 1
        assert len(out['unplaced_candidates']) == 1

    async def test_stages_sorted_by_name(self, db):
        await self._seed()
        out = await ReportsService().stage_utilization(DAY_START, DAY_END, actor=(await _actor()))
        assert [r['stage_name'] for r in out['stages']] == ['Alpha', 'Beta']

    async def test_stage_id_filter(self, db):
        t, alpha, beta, inactive = await self._seed()
        out = await ReportsService().stage_utilization(
            DAY_START, DAY_END, stage_id=alpha.id, actor=(await _actor()),
        )
        assert [r['stage_id'] for r in out['stages']] == [alpha.id]

    async def test_tournament_filter(self, db):
        t1 = await Tournament.create(name='A', average_match_duration=60)
        t2 = await Tournament.create(name='B', average_match_duration=60)
        alpha = await Stage.create(name='Alpha')
        await Match.create(
            tournament=t1, stage=alpha,
            scheduled_at=utc(2025, 10, 23, 18, 0),
            seated_at=utc(2025, 10, 23, 18, 0),
            finished_at=utc(2025, 10, 23, 20, 0),
        )
        await Match.create(
            tournament=t2, stage=alpha,
            scheduled_at=utc(2025, 10, 23, 21, 0),
            seated_at=utc(2025, 10, 23, 21, 0),
            finished_at=utc(2025, 10, 23, 23, 0),
        )
        out = await ReportsService().stage_utilization(
            DAY_START, DAY_END, tournament_id=t1.id, actor=(await _actor()),
        )
        stages = {r['stage_id']: r for r in out['stages']}
        # Only t1's 2-hour block is counted.
        assert stages[alpha.id]['scheduled_hours'] == 2.0
        assert len(stages[alpha.id]['matches']) == 1


async def test_reports_refuse_a_plain_member(db):
    """The service is the gate, so a new caller that forgets the check still can't read."""
    member = await create_community_member(discord_id=88001, username='member')
    with pytest.raises(PermissionError):
        await ReportsService().crew_coverage(DAY_START, DAY_END, actor=member)
    with pytest.raises(PermissionError):
        await ReportsService().stage_utilization(DAY_START, DAY_END, actor=member)
