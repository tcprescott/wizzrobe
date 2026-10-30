"""Match operations report: lateness judged by check-in, and the outlier guard.

The race-start figures are covered in ``test_reports_service_db.py``.
"""

from datetime import datetime

from application.services.reports_service import ReportsService
from application.utils.timezone import EASTERN_TZ
from models import Match, Stage, Tournament
from tests.factories import utc

DAY_START = datetime(2025, 10, 23, tzinfo=EASTERN_TZ)
DAY_END = datetime(2025, 10, 24, tzinfo=EASTERN_TZ)


async def _actor():
    from application.services.user_service import UserService
    return await UserService().get_system_user()


class TestMatchOperationsCheckIn:
    """Lateness judged by check-in (``seated_at``), and the outlier guard."""

    async def _seed(self):
        t = await Tournament.create(name='Cup', average_match_duration=90)
        stage = await Stage.create(name='Alpha')
        early = await Match.create(
            tournament=t, stage=stage,
            scheduled_at=utc(2025, 10, 23, 14, 0),
            seated_at=utc(2025, 10, 23, 13, 57),
            started_at=utc(2025, 10, 23, 14, 9),
            finished_at=utc(2025, 10, 23, 15, 30),
        )
        on_time = await Match.create(
            tournament=t,
            scheduled_at=utc(2025, 10, 23, 15, 0),
            seated_at=utc(2025, 10, 23, 15, 0),
            started_at=utc(2025, 10, 23, 15, 10),
            finished_at=utc(2025, 10, 23, 16, 40),
        )
        late = await Match.create(
            tournament=t,
            scheduled_at=utc(2025, 10, 23, 16, 0),
            seated_at=utc(2025, 10, 23, 16, 20),
            started_at=utc(2025, 10, 23, 16, 26),
        )
        unseated = await Match.create(tournament=t, scheduled_at=utc(2025, 10, 23, 17, 0))
        return t, stage, early, on_time, late, unseated

    async def test_rows_measure_check_in_and_setup(self, db):
        _, _, early, on_time, late, unseated = await self._seed()
        out = await ReportsService().match_operations(DAY_START, DAY_END, actor=(await _actor()))
        rows = {r['match_id']: r for r in out['rows']}

        assert rows[early.id]['seat_delay_min'] == -3
        assert rows[early.id]['setup_min'] == 12
        assert rows[early.id]['start_delay_min'] == 9
        assert rows[early.id]['staged'] is True

        assert rows[on_time.id]['seat_delay_min'] == 0
        assert rows[on_time.id]['setup_min'] == 10
        assert rows[on_time.id]['staged'] is False

        assert rows[late.id]['seat_delay_min'] == 20
        assert rows[late.id]['setup_min'] == 6

        assert rows[unseated.id]['seat_delay_min'] is None
        assert rows[unseated.id]['setup_min'] is None
        assert rows[unseated.id]['start_delay_min'] is None
        assert out['flagged_match_ids'] == []

    async def test_aggregates_split_by_stage(self, db):
        t, *_ = await self._seed()
        out = await ReportsService().match_operations(DAY_START, DAY_END, actor=(await _actor()))
        a = {x['tournament_id']: x for x in out['aggregates']}[t.id]

        assert a['avg_seat_delay_min'] == round((-3 + 0 + 20) / 3, 1)
        assert a['seated_on_time_pct'] == round(2 / 3 * 100, 1)
        assert a['avg_setup_min'] == round((12 + 10 + 6) / 3, 1)
        assert a['avg_start_delay_min'] == round((9 + 10 + 26) / 3, 1)
        assert a['on_time_pct'] == 0.0

        assert a['staged_avg_seat_delay_min'] == -3.0
        assert a['staged_seated_on_time_pct'] == 100.0
        assert a['staged_avg_setup_min'] == 12.0
        assert a['unstaged_avg_seat_delay_min'] == 10.0
        assert a['unstaged_seated_on_time_pct'] == 50.0
        assert a['unstaged_avg_setup_min'] == 8.0

    async def test_empty_slice_reports_none(self, db):
        t = await Tournament.create(name='Cup')
        await Match.create(tournament=t, scheduled_at=utc(2025, 10, 23, 17, 0))
        out = await ReportsService().match_operations(DAY_START, DAY_END, actor=(await _actor()))
        a = out['aggregates'][0]
        for prefix in ('', 'staged_', 'unstaged_'):
            assert a[f'{prefix}avg_seat_delay_min'] is None
            assert a[f'{prefix}seated_on_time_pct'] is None
            assert a[f'{prefix}avg_setup_min'] is None

    async def test_implausible_matches_flagged_and_left_out(self, db):
        t, _, early, on_time, late, _ = await self._seed()
        clicked = utc(2025, 10, 22, 18, 52)
        closed_early = await Match.create(
            tournament=t,
            scheduled_at=utc(2025, 10, 23, 18, 0),
            seated_at=clicked, started_at=clicked, finished_at=clicked,
        )
        closed_on_slot = await Match.create(
            tournament=t,
            scheduled_at=utc(2025, 10, 23, 19, 0),
            seated_at=utc(2025, 10, 23, 19, 2),
            started_at=utc(2025, 10, 23, 19, 2),
            finished_at=utc(2025, 10, 23, 19, 2),
        )
        way_early = await Match.create(
            tournament=t,
            scheduled_at=utc(2025, 10, 23, 20, 0),
            started_at=utc(2025, 10, 23, 13, 0),
            finished_at=utc(2025, 10, 23, 14, 30),
        )
        out = await ReportsService().match_operations(DAY_START, DAY_END, actor=(await _actor()))

        assert sorted(out['flagged_match_ids']) == sorted([closed_early.id, closed_on_slot.id, way_early.id])
        rows = {r['match_id']: r for r in out['rows']}
        assert rows[closed_early.id]['start_delay_min'] < -1000
        assert len(rows) == 7

        a = {x['tournament_id']: x for x in out['aggregates']}[t.id]
        assert a['matches_total'] == 7
        assert a['matches_started'] == 6
        assert a['avg_start_delay_min'] == round((9 + 10 + 26) / 3, 1)
        assert a['avg_duration_min'] == round((81 + 90) / 2, 1)
        assert a['on_time_pct'] == 0.0
        assert a['avg_seat_delay_min'] == round((-3 + 0 + 20) / 3, 1)
        assert a['seated_on_time_pct'] == round(2 / 3 * 100, 1)
        assert a['avg_setup_min'] == round((12 + 10 + 6) / 3, 1)
        assert a['unstaged_avg_seat_delay_min'] == 10.0

    async def test_six_hours_early_is_still_plausible(self, db):
        t = await Tournament.create(name='Cup')
        m = await Match.create(
            tournament=t,
            scheduled_at=utc(2025, 10, 23, 20, 0),
            seated_at=utc(2025, 10, 23, 14, 0),
            started_at=utc(2025, 10, 23, 14, 5),
        )
        out = await ReportsService().match_operations(DAY_START, DAY_END, actor=(await _actor()))
        assert out['flagged_match_ids'] == []
        assert out['aggregates'][0]['avg_seat_delay_min'] == -360.0
        assert m.id in {r['match_id'] for r in out['rows']}

    async def test_late_finish_and_zero_length_left_out_of_length_only(self, db):
        t, *_ = await self._seed()
        late_click = await Match.create(
            tournament=t,
            scheduled_at=utc(2025, 10, 23, 21, 0),
            started_at=utc(2025, 10, 23, 21, 10),
            finished_at=utc(2025, 10, 24, 3, 41),
        )
        zero = await Match.create(
            tournament=t,
            scheduled_at=utc(2025, 10, 23, 22, 0),
            started_at=utc(2025, 10, 23, 22, 4),
            finished_at=utc(2025, 10, 23, 22, 4),
        )
        long_race = await Match.create(
            tournament=t,
            scheduled_at=utc(2025, 10, 23, 23, 0),
            started_at=utc(2025, 10, 23, 23, 0),
            finished_at=utc(2025, 10, 24, 3, 30),
        )
        out = await ReportsService().match_operations(DAY_START, DAY_END, actor=(await _actor()))

        assert sorted(out['duration_outlier_ids']) == sorted([late_click.id, zero.id])
        assert out['flagged_match_ids'] == []
        rows = {r['match_id']: r for r in out['rows']}
        assert rows[late_click.id]['duration_min'] == 391
        assert long_race.id not in out['duration_outlier_ids']

        a = {x['tournament_id']: x for x in out['aggregates']}[t.id]
        assert a['avg_duration_min'] == round((81 + 90 + 270) / 3, 1)
        assert a['avg_start_delay_min'] == round((9 + 10 + 26 + 10 + 4 + 0) / 6, 1)

    async def test_no_configured_length_only_drops_zero(self, db):
        t = await Tournament.create(name='Open')
        m = await Match.create(
            tournament=t,
            scheduled_at=utc(2025, 10, 23, 14, 0),
            started_at=utc(2025, 10, 23, 14, 0),
            finished_at=utc(2025, 10, 23, 23, 0),
        )
        out = await ReportsService().match_operations(DAY_START, DAY_END, actor=(await _actor()))
        assert out['duration_outlier_ids'] == []
        assert out['aggregates'][0]['avg_duration_min'] == 540.0
        assert m.id in {r['match_id'] for r in out['rows']}
