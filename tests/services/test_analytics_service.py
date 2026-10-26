"""Tests for AnalyticsService.

Pure bucketing / health-score helpers are tested without a DB; the four
aggregations are exercised against the in-memory SQLite ``db`` fixture.
"""

from datetime import date, datetime, timezone

import pytest

from application.services.analytics_service import (
    HEALTH_WEIGHTS,
    MAX_BUCKETS,
    AnalyticsService,
)
from application.utils.timezone import EASTERN_TZ
from models import (
    AuditLog,
    Commentator,
    Match,
    Tournament,
    Tracker,
    User,
    VolunteerAssignment,
    VolunteerPosition,
    VolunteerShift,
)

UTC = timezone.utc


from tests.factories import utc


async def _actor():
    """Reports gate their reader; these tests read as the system actor."""
    from application.services.user_service import UserService
    return await UserService().get_system_user()


def to_display(y, mo, d, h=0, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=EASTERN_TZ)


# ---------------------------------------------------------------------------
# bucket_start
# ---------------------------------------------------------------------------


class TestBucketStart:
    def test_week_snaps_to_monday(self):
        # 2025-10-23 is a Thursday; Monday of that week is 2025-10-20.
        assert AnalyticsService.bucket_start(date(2025, 10, 23), 'week') == date(2025, 10, 20)

    def test_week_on_monday_is_identity(self):
        assert AnalyticsService.bucket_start(date(2025, 10, 20), 'week') == date(2025, 10, 20)

    def test_month_snaps_to_first(self):
        assert AnalyticsService.bucket_start(date(2025, 10, 23), 'month') == date(2025, 10, 1)


# ---------------------------------------------------------------------------
# iter_bucket_starts
# ---------------------------------------------------------------------------


class TestIterBucketStarts:
    def test_weekly_contiguous(self):
        starts = AnalyticsService.iter_bucket_starts(date(2025, 10, 1), date(2025, 11, 1), 'week')
        assert starts == [
            date(2025, 9, 29), date(2025, 10, 6), date(2025, 10, 13),
            date(2025, 10, 20), date(2025, 10, 27),
        ]

    def test_monthly_contiguous_across_year_boundary(self):
        starts = AnalyticsService.iter_bucket_starts(date(2025, 11, 15), date(2026, 2, 3), 'month')
        assert starts == [date(2025, 11, 1), date(2025, 12, 1), date(2026, 1, 1), date(2026, 2, 1)]

    def test_single_bucket_when_same_week(self):
        starts = AnalyticsService.iter_bucket_starts(date(2025, 10, 20), date(2025, 10, 24), 'week')
        assert starts == [date(2025, 10, 20)]

    def test_reversed_range_is_clamped(self):
        starts = AnalyticsService.iter_bucket_starts(date(2025, 10, 20), date(2025, 10, 1), 'week')
        assert starts == [date(2025, 10, 20)]

    def test_capped_at_max_buckets(self):
        starts = AnalyticsService.iter_bucket_starts(date(2000, 1, 1), date(2030, 1, 1), 'week')
        assert len(starts) == MAX_BUCKETS

    def test_cap_keeps_most_recent_buckets(self):
        # When capped, the newest bucket must still reach the end of the range —
        # it is the oldest buckets that get dropped, not the newest.
        starts = AnalyticsService.iter_bucket_starts(date(2000, 1, 1), date(2030, 1, 6), 'week')
        assert starts[-1] == AnalyticsService.bucket_start(date(2030, 1, 6), 'week')
        assert len(starts) == MAX_BUCKETS

    def test_monthly_cap_keeps_most_recent(self):
        starts = AnalyticsService.iter_bucket_starts(date(1900, 1, 1), date(2030, 6, 15), 'month')
        assert starts[-1] == date(2030, 6, 1)
        assert len(starts) == MAX_BUCKETS


# ---------------------------------------------------------------------------
# bucket_label / _bucket_index
# ---------------------------------------------------------------------------


class TestBucketLabel:
    def test_week_label_is_iso_date(self):
        assert AnalyticsService.bucket_label(date(2025, 10, 20), 'week') == '2025-10-20'

    def test_month_label_is_year_month(self):
        assert AnalyticsService.bucket_label(date(2025, 10, 1), 'month') == '2025-10'


class TestBucketIndex:
    def _starts(self):
        return AnalyticsService.iter_bucket_starts(date(2025, 10, 1), date(2025, 11, 1), 'week')

    def test_maps_instant_to_correct_bucket(self):
        starts = self._starts()
        index_map = {d: i for i, d in enumerate(starts)}
        # 2025-10-22 16:00 UTC = 12:00 ET → week of Oct 20 → index 3
        assert AnalyticsService._bucket_index(index_map, utc(2025, 10, 22, 16), 'week') == 3

    def test_out_of_range_returns_none(self):
        starts = self._starts()
        index_map = {d: i for i, d in enumerate(starts)}
        assert AnalyticsService._bucket_index(index_map, utc(2030, 1, 1), 'week') is None

    def test_none_instant_returns_none(self):
        assert AnalyticsService._bucket_index({}, None, 'week') is None

    def test_uses_eastern_date_not_utc_date(self):
        starts = AnalyticsService.iter_bucket_starts(date(2025, 10, 1), date(2025, 10, 31), 'week')
        index_map = {d: i for i, d in enumerate(starts)}
        # 2025-10-20 02:00 UTC is still 2025-10-19 (Sun) 22:00 ET → week of Oct 13.
        idx = AnalyticsService._bucket_index(index_map, utc(2025, 10, 20, 2), 'week')
        assert starts[idx] == date(2025, 10, 13)


# ---------------------------------------------------------------------------
# health_score
# ---------------------------------------------------------------------------


class TestHealthScore:
    def test_single_component(self):
        assert AnalyticsService.health_score([(1.0, 0.3)]) == 100.0

    def test_renormalizes_over_present_components(self):
        # Only two of four dimensions have data; weights renormalize so the
        # score is their weighted average, not diluted by the absent ones.
        score = AnalyticsService.health_score([(1.0, 0.30), (0.0, 0.25)])
        expected = round((1.0 * 0.30 + 0.0 * 0.25) / (0.30 + 0.25) * 100, 1)
        assert score == expected

    def test_clamps_values_to_unit_interval(self):
        assert AnalyticsService.health_score([(1.5, 0.5)]) == 100.0
        assert AnalyticsService.health_score([(-0.5, 0.5)]) == 0.0

    def test_empty_is_none(self):
        assert AnalyticsService.health_score([]) is None

    def test_all_none_values_is_none(self):
        assert AnalyticsService.health_score([(None, 0.3), (None, 0.25)]) is None

    def test_zero_weight_ignored(self):
        assert AnalyticsService.health_score([(0.5, 0.0)]) is None


# ---------------------------------------------------------------------------
# _finalize_health (pure, deterministic — no now())
# ---------------------------------------------------------------------------


def _stats(**overrides):
    base = {
        'tournament_id': 1,
        'tournament_name': 'T',
        'expected_avg_min': 90,
        'matches_total': 0,
        'matches_past': 0,
        'matches_started': 0,
        'matches_finished': 0,
        'matches_finished_past': 0,
        'on_time_count': 0,
        '_start_delay_sum': 0,
        '_start_delay_seen': 0,
        '_duration_sum': 0,
        '_duration_seen': 0,
        'stream_candidates': 0,
        'candidates_covered': 0,
    }
    base.update(overrides)
    return base


class TestFinalizeHealth:
    def test_completion_only_from_past_matches(self):
        row = AnalyticsService._finalize_health(
            _stats(matches_past=4, matches_finished=3, matches_finished_past=3)
        )
        assert row['completion_pct'] == 75.0

    def test_completion_capped_when_finished_exceeds_past(self):
        # A match finished then rescheduled into the future inflates
        # matches_finished but not matches_finished_past; completion stays <= 100.
        row = AnalyticsService._finalize_health(
            _stats(matches_total=2, matches_past=1, matches_finished=2, matches_finished_past=1)
        )
        assert row['completion_pct'] == 100.0

    def test_no_past_matches_leaves_completion_none(self):
        row = AnalyticsService._finalize_health(_stats(matches_total=5, matches_past=0))
        assert row['completion_pct'] is None

    def test_on_time_rate(self):
        row = AnalyticsService._finalize_health(
            _stats(_start_delay_seen=4, on_time_count=3, _start_delay_sum=40)
        )
        assert row['on_time_pct'] == 75.0
        assert row['avg_start_delay_min'] == 10.0

    def test_coverage_rate(self):
        row = AnalyticsService._finalize_health(_stats(stream_candidates=4, candidates_covered=1))
        assert row['coverage_pct'] == 25.0

    def test_duration_adherence_perfect_when_matches_expected(self):
        # avg == expected → adherence 1.0; this is the only present component.
        row = AnalyticsService._finalize_health(
            _stats(_duration_seen=2, _duration_sum=180, expected_avg_min=90)
        )
        assert row['avg_duration_min'] == 90.0
        assert row['health_score'] == 100.0

    def test_no_data_yields_none_score(self):
        row = AnalyticsService._finalize_health(_stats())
        assert row['health_score'] is None

    def test_combined_score_weights(self):
        row = AnalyticsService._finalize_health(_stats(
            matches_past=2, matches_finished=2, matches_finished_past=2,  # completion 1.0
            _start_delay_seen=2, on_time_count=1, _start_delay_sum=23,  # on-time 0.5
            stream_candidates=2, candidates_covered=1,   # coverage 0.5
            _duration_sum=178, _duration_seen=2, expected_avg_min=90,   # adherence ~0.989
        ))
        adherence = 1 - abs(89 - 90) / 90
        expected = round((
            1.0 * HEALTH_WEIGHTS['completion']
            + 0.5 * HEALTH_WEIGHTS['on_time']
            + 0.5 * HEALTH_WEIGHTS['coverage']
            + adherence * HEALTH_WEIGHTS['duration']
        ) / sum(HEALTH_WEIGHTS.values()) * 100, 1)
        assert row['health_score'] == expected


# ---------------------------------------------------------------------------
# _duration_hours / _normalize_bucket
# ---------------------------------------------------------------------------


class TestDurationHours:
    def test_normal(self):
        assert AnalyticsService._duration_hours(utc(2025, 1, 1, 12), utc(2025, 1, 1, 15)) == 3.0

    def test_negative_clamped_to_zero(self):
        assert AnalyticsService._duration_hours(utc(2025, 1, 1, 15), utc(2025, 1, 1, 12)) == 0.0

    def test_none_is_zero(self):
        assert AnalyticsService._duration_hours(None, utc(2025, 1, 1, 12)) == 0.0


class TestNormalizeBucket:
    def test_month_passthrough(self):
        assert AnalyticsService._normalize_bucket('month') == 'month'

    def test_anything_else_is_week(self):
        assert AnalyticsService._normalize_bucket('week') == 'week'
        assert AnalyticsService._normalize_bucket(None) == 'week'
        assert AnalyticsService._normalize_bucket('garbage') == 'week'


# ---------------------------------------------------------------------------
# DB-backed aggregations
# ---------------------------------------------------------------------------


WINDOW_START = to_display(2025, 10, 1)
WINDOW_END = to_display(2025, 11, 1)
# Weekly buckets over [Oct 1, Nov 1]: [Sep 29, Oct 6, Oct 13, Oct 20, Oct 27]
BUCKET_EARLY = 1   # week of Oct 6
BUCKET_LATE = 3    # week of Oct 20


async def _user(discord_id, name):
    return await User.create(discord_id=discord_id, username=name, display_name=name)


class TestCrewParticipationTrends:
    async def test_buckets_signups_and_unique_people(self, db):
        t = await Tournament.create(name='Cup')
        u1 = await _user(1, 'Alice')
        u2 = await _user(2, 'Bob')
        u3 = await _user(3, 'Cara')
        # match A in the Oct 6 week, match B in the Oct 20 week
        m_a = await Match.create(tournament=t, scheduled_at=utc(2025, 10, 8, 16))
        m_b = await Match.create(tournament=t, scheduled_at=utc(2025, 10, 22, 16))

        await Commentator.create(user=u1, match=m_a, approved=True)
        await Commentator.create(user=u2, match=m_a, approved=False)
        await Tracker.create(user=u1, match=m_a, approved=True)
        await Commentator.create(user=u1, match=m_b, approved=True)
        await Tracker.create(user=u3, match=m_b, approved=True)

        out = await AnalyticsService().crew_participation_trends(WINDOW_START, WINDOW_END, 'week', actor=(await _actor()))

        assert out['commentator_approved'] == [0, 1, 0, 1, 0]
        assert out['tracker_approved'] == [0, 1, 0, 1, 0]
        assert out['commentator_signups'] == [0, 2, 0, 1, 0]
        assert out['unique_people'] == [0, 2, 0, 2, 0]
        assert out['totals']['unique_people'] == 3
        assert out['totals']['commentator_approved'] == 2
        # Alice: 2 commentary + 1 tracker approved = 3, the top contributor
        top = out['top_contributors'][0]
        assert top['name'] == 'Alice'
        assert top['total_approved'] == 3

    async def test_tournament_filter(self, db):
        t1 = await Tournament.create(name='A')
        t2 = await Tournament.create(name='B')
        u = await _user(1, 'Alice')
        m1 = await Match.create(tournament=t1, scheduled_at=utc(2025, 10, 8, 16))
        m2 = await Match.create(tournament=t2, scheduled_at=utc(2025, 10, 8, 16))
        await Commentator.create(user=u, match=m1, approved=True)
        await Commentator.create(user=u, match=m2, approved=True)

        out = await AnalyticsService().crew_participation_trends(
            WINDOW_START, WINDOW_END, 'week', tournament_id=t1.id, actor=(await _actor()),
        )
        assert out['totals']['commentator_approved'] == 1

    async def test_end_is_exclusive(self, db):
        # A match scheduled exactly at the window's exclusive end must be excluded
        # and must not create a spurious trailing bucket.
        t = await Tournament.create(name='Cup')
        u = await _user(1, 'Alice')
        m = await Match.create(tournament=t, scheduled_at=WINDOW_END)
        await Commentator.create(user=u, match=m, approved=True)

        out = await AnalyticsService().crew_participation_trends(WINDOW_START, WINDOW_END, 'week', actor=(await _actor()))
        assert len(out['bucket_labels']) == 5  # [Sep29, Oct6, Oct13, Oct20, Oct27]
        assert out['totals']['commentator_approved'] == 0


class TestVolunteerHourTrends:
    async def test_scheduled_checked_in_and_fill(self, db):
        pos = await VolunteerPosition.create(name='Check-in', color='#fff')
        u_a = await _user(1, 'Alice')
        u_b = await _user(2, 'Bob')

        s1 = await VolunteerShift.create(
            position=pos, starts_at=utc(2025, 10, 8, 12), ends_at=utc(2025, 10, 8, 16),
            slots_needed=2,
        )  # 4h, Oct 6 week
        s2 = await VolunteerShift.create(
            position=pos, starts_at=utc(2025, 10, 22, 12), ends_at=utc(2025, 10, 22, 14),
            slots_needed=3,
        )  # 2h, Oct 20 week

        await VolunteerAssignment.create(shift=s1, user=u_a, checked_in_at=utc(2025, 10, 8, 12))
        await VolunteerAssignment.create(shift=s1, user=u_b)
        await VolunteerAssignment.create(shift=s2, user=u_a, checked_in_at=utc(2025, 10, 22, 12))

        out = await AnalyticsService().volunteer_hour_trends(WINDOW_START, WINDOW_END, 'week', actor=(await _actor()))

        assert out['scheduled_hours'] == [0, 8.0, 0, 2.0, 0]
        assert out['checked_in_hours'] == [0, 4.0, 0, 2.0, 0]
        assert out['needed_hours'] == [0, 8.0, 0, 6.0, 0]
        assert out['fill_rate'][BUCKET_EARLY] == 100.0
        assert out['fill_rate'][BUCKET_LATE] == pytest.approx(33.3, abs=0.1)
        assert out['fill_rate'][0] is None
        assert out['totals']['scheduled_hours'] == 10.0
        assert out['totals']['volunteers'] == 2
        # Alice worked both shifts (6h), the top volunteer
        assert out['top_volunteers'][0]['name'] == 'Alice'
        assert out['top_volunteers'][0]['scheduled_hours'] == 6.0
        assert out['positions'][0]['hours'] == [0, 8.0, 0, 2.0, 0]


class TestTournamentHealthDB:
    async def test_health_scorecard(self, db):
        t = await Tournament.create(name='Cup', average_match_duration=90)
        u1 = await _user(1, 'Alice')
        u2 = await _user(2, 'Bob')

        # M1: on-time (3 min late), finished 88 min, fully crewed
        m1 = await Match.create(
            tournament=t, scheduled_at=utc(2025, 10, 8, 16),
            started_at=utc(2025, 10, 8, 16, 3), finished_at=utc(2025, 10, 8, 17, 31),
            is_stream_candidate=True,
        )
        await Commentator.create(user=u1, match=m1, approved=True)
        await Tracker.create(user=u2, match=m1, approved=True)

        # M2: 20 min late, finished 90 min, only commentator approved
        m2 = await Match.create(
            tournament=t, scheduled_at=utc(2025, 10, 9, 16),
            started_at=utc(2025, 10, 9, 16, 20), finished_at=utc(2025, 10, 9, 17, 50),
            is_stream_candidate=True,
        )
        await Commentator.create(user=u1, match=m2, approved=True)
        await Tracker.create(user=u2, match=m2, approved=False)

        out = await AnalyticsService().tournament_health(WINDOW_START, WINDOW_END, actor=(await _actor()))
        row = next(r for r in out['rows'] if r['tournament_id'] == t.id)

        assert row['matches_total'] == 2
        assert row['matches_finished'] == 2
        assert row['completion_pct'] == 100.0
        assert row['on_time_pct'] == 50.0
        assert row['coverage_pct'] == 50.0
        # completion 1.0, on-time 0.5, coverage 0.5, adherence 1-1/90 → 74.8
        assert row['health_score'] == pytest.approx(74.8, abs=0.1)

    async def test_coverage_follows_the_tournament_crew_requirement(self, db):
        # Health used to require an approved tracker on every stream candidate,
        # so a commentary-only tournament carried a permanently depressed score.
        t = await Tournament.create(
            name='Comms only', average_match_duration=90,
            required_commentators=1, required_trackers=0,
        )
        u1 = await _user(1, 'Alice')
        for day in (8, 9):
            match = await Match.create(
                tournament=t, scheduled_at=utc(2025, 10, day, 16),
                started_at=utc(2025, 10, day, 16), finished_at=utc(2025, 10, day, 17, 30),
                is_stream_candidate=True,
            )
            await Commentator.create(user=u1, match=match, approved=True)

        out = await AnalyticsService().tournament_health(WINDOW_START, WINDOW_END, actor=(await _actor()))
        row = next(r for r in out['rows'] if r['tournament_id'] == t.id)
        assert row['coverage_pct'] == 100.0

    async def test_future_finished_match_does_not_inflate_completion(self, db):
        # A match with finished_at set but scheduled far in the future must not
        # push completion above 100% (it is not counted as a past match).
        t = await Tournament.create(name='Cup', average_match_duration=90)
        await Match.create(
            tournament=t, scheduled_at=utc(2025, 10, 8, 16),
            started_at=utc(2025, 10, 8, 16, 2), finished_at=utc(2025, 10, 8, 17, 30),
        )
        await Match.create(
            tournament=t, scheduled_at=utc(2099, 10, 9, 16),
            started_at=utc(2099, 10, 9, 16, 5), finished_at=utc(2099, 10, 9, 17, 35),
        )
        out = await AnalyticsService().tournament_health(
            to_display(2025, 10, 1), to_display(2100, 1, 1), actor=(await _actor()),
        )
        row = next(r for r in out['rows'] if r['tournament_id'] == t.id)
        assert row['matches_finished'] == 2
        assert row['matches_past'] == 1
        assert row['completion_pct'] == 100.0

    async def test_tournament_filter(self, db):
        t1 = await Tournament.create(name='A')
        t2 = await Tournament.create(name='B')
        await Match.create(tournament=t1, scheduled_at=utc(2025, 10, 8, 16))
        await Match.create(tournament=t2, scheduled_at=utc(2025, 10, 8, 16))
        out = await AnalyticsService().tournament_health(
            WINDOW_START, WINDOW_END, tournament_id=t1.id, actor=(await _actor()),
        )
        assert len(out['rows']) == 1
        assert out['rows'][0]['tournament_id'] == t1.id


class TestActivityTrendsDB:
    async def test_grouped_by_category_and_bucket(self, db):
        u = await _user(1, 'Alice')
        logs = [
            ('match.created', utc(2025, 10, 8, 16)),
            ('match.updated', utc(2025, 10, 8, 17)),
            ('crew.signup_created', utc(2025, 10, 22, 16)),
        ]
        for action, created in logs:
            log = await AuditLog.create(user=u, action=action, details='{}')
            # created_at is auto_now_add; override it after insert for bucketing.
            await AuditLog.filter(id=log.id).update(created_at=created)

        out = await AnalyticsService().activity_trends(WINDOW_START, WINDOW_END, 'week', actor=(await _actor()))

        assert out['total'] == 3
        by_cat = {c['category']: c for c in out['categories']}
        assert by_cat['match']['counts'] == [0, 2, 0, 0, 0]
        assert by_cat['crew']['counts'] == [0, 0, 0, 1, 0]
        # sorted by total volume descending
        assert out['categories'][0]['category'] == 'match'


# ---------------------------------------------------------------------------
# activity_extent — what the Insights page opens on
# ---------------------------------------------------------------------------


class TestActivityExtent:
    async def test_empty_tenant_has_no_extent(self, db):
        assert await AnalyticsService().activity_extent(actor=(await _actor())) == (None, None)

    async def test_spans_every_source(self, db):
        t = await Tournament.create(name='T')
        await Match.create(tournament=t, scheduled_at=utc(2025, 10, 8, 16))
        position = await VolunteerPosition.create(name='Desk')
        await VolunteerShift.create(
            position=position,
            starts_at=utc(2025, 11, 1, 12),
            ends_at=utc(2025, 11, 1, 16),
            slots_needed=1,
        )
        u = await _user(1, 'Alice')
        log = await AuditLog.create(user=u, action='match.created', details='{}')
        await AuditLog.filter(id=log.id).update(created_at=utc(2025, 9, 1, 12))

        first, last = await AnalyticsService().activity_extent(actor=(await _actor()))

        # Eastern dates: the audit row is the earliest, the shift the latest.
        assert first == date(2025, 9, 1)
        assert last == date(2025, 11, 1)

    async def test_single_day_of_history_is_a_single_day_extent(self, db):
        t = await Tournament.create(name='T')
        await Match.create(tournament=t, scheduled_at=utc(2025, 10, 8, 16))
        await Match.create(tournament=t, scheduled_at=utc(2025, 10, 8, 20))

        assert await AnalyticsService().activity_extent(actor=(await _actor())) == (
            date(2025, 10, 8), date(2025, 10, 8),
        )

    async def test_tournament_scope_ignores_unscoped_sources(self, db):
        """Shifts and audit logs carry no tournament, so a narrowed extent must
        come only from the sources that do — otherwise it widens back out."""
        t1 = await Tournament.create(name='A')
        t2 = await Tournament.create(name='B')
        await Match.create(tournament=t1, scheduled_at=utc(2025, 10, 8, 16))
        await Match.create(tournament=t2, scheduled_at=utc(2025, 12, 8, 16))
        position = await VolunteerPosition.create(name='Desk')
        await VolunteerShift.create(
            position=position,
            starts_at=utc(2026, 3, 1, 12),
            ends_at=utc(2026, 3, 1, 16),
            slots_needed=1,
        )

        assert await AnalyticsService().activity_extent(tournament_id=t1.id, actor=(await _actor())) == (
            date(2025, 10, 8), date(2025, 10, 8),
        )

    async def test_unscheduled_matches_are_not_an_extent(self, db):
        t = await Tournament.create(name='T')
        await Match.create(tournament=t, scheduled_at=None)

        assert await AnalyticsService().activity_extent(tournament_id=t.id, actor=(await _actor())) == (None, None)


class TestOutlierDuration:
    """The one rule both length averages use to skip late Finish clicks."""

    def test_rule(self):
        from types import SimpleNamespace

        from application.services.reporting_shared import is_outlier_duration
        t = SimpleNamespace(average_match_duration=100)
        assert is_outlier_duration(0, t)
        assert is_outlier_duration(301, t)
        assert not is_outlier_duration(300, t)
        assert not is_outlier_duration(None, t)
        assert not is_outlier_duration(900, SimpleNamespace(average_match_duration=None))
        assert not is_outlier_duration(900, None)
