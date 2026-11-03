"""REST API tests for the reporting and telemetry endpoints.

Covers ``api/routers/reports.py`` (admin) and ``api/routers/telemetry.py``
(staff). The two reshaped reports are asserted on their *shape* — that they
summarize instead of handing back the chart series — because that is the whole
reason they are not pass-throughs.
"""

from datetime import datetime, timedelta, timezone

from application.tenant_context import tenant_scope
from models import (
    FeatureFlag,
    Match,
    Role,
    Stage,
    TelemetryEvent,
    TenantFeatureFlag,
    Tournament,
)
from tests.api_helpers import client_for, create_user_token

NOW = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
WINDOW = {'start': (NOW - timedelta(days=1)).isoformat(), 'end': (NOW + timedelta(days=1)).isoformat()}


async def _scheduled_match(name='Cup', at=NOW, stage=None):
    tournament = await Tournament.create(name=name)
    return await Match.create(tournament=tournament, scheduled_at=at, stage=stage)


class TestGate:
    async def test_a_plain_member_is_forbidden(self, db, app):
        _, raw = await create_user_token(username='plain')
        async with client_for(app, raw) as c:
            assert (await c.get('/api/reports/activity-trends', params=WINDOW)).status_code == 403

    async def test_unauthenticated_is_401(self, db, app):
        async with client_for(app) as c:
            assert (await c.get('/api/reports/activity-trends', params=WINDOW)).status_code == 401

    async def test_a_crew_coordinator_reaches_the_reports(self, db, app):
        """``can_view_admin`` is the gate, so a tournament's crew coordinator passes."""
        user, raw = await create_user_token(username='cc')
        tournament = await Tournament.create(name='Cup')
        await tournament.crew_coordinators.add(user)
        async with client_for(app, raw) as c:
            assert (await c.get('/api/reports/activity-trends', params=WINDOW)).status_code == 200

    async def test_single_tab_admin_roles_are_refused_the_reports(self, db, app):
        """An equipment manager or volunteer coordinator has no Reports tab for
        matches, crew or stages, so the API refuses those reports too."""
        for role in (Role.EQUIPMENT_MANAGER, Role.VOLUNTEER_COORDINATOR):
            _, raw = await create_user_token(username=role.value, roles=[role])
            async with client_for(app, raw) as c:
                for path in ('/api/reports/activity-trends', '/api/reports/crew-coverage'):
                    assert (await c.get(path, params=WINDOW)).status_code == 403, (role, path)

    async def test_a_volunteer_coordinator_reads_volunteer_hours_only(self, db, app):
        _, raw = await create_user_token(username='vc', roles=[Role.VOLUNTEER_COORDINATOR])
        async with client_for(app, raw) as c:
            assert (await c.get('/api/reports/volunteer-hour-trends', params=WINDOW)).status_code == 200
        _, raw = await create_user_token(username='em', roles=[Role.EQUIPMENT_MANAGER])
        async with client_for(app, raw) as c:
            assert (await c.get('/api/reports/volunteer-hour-trends', params=WINDOW)).status_code == 403

    async def test_a_read_only_token_still_reads(self, db, app):
        _, raw = await create_user_token(username='staff', roles=[Role.STAFF], read_only=True)
        async with client_for(app, raw) as c:
            assert (await c.get('/api/reports/activity-trends', params=WINDOW)).status_code == 200


class TestReshapedReports:
    async def test_capacity_forecast_summarizes_instead_of_dumping_intervals(self, db, app):
        _, raw = await create_user_token(username='staff', roles=[Role.STAFF])
        await _scheduled_match()
        async with client_for(app, raw) as c:
            body = (await c.get('/api/reports/capacity-forecast', params=WINDOW)).json()
        assert 'intervals' not in body and 'player_counts' not in body
        assert set(body) >= {
            'peak_players', 'over_capacity_intervals', 'busiest', 'interval_minutes',
        }
        assert len(body['busiest']) <= 5

    async def test_stage_utilization_reports_per_stage_totals(self, db, app):
        _, raw = await create_user_token(username='staff', roles=[Role.STAFF])
        stage = await Stage.create(name='Main')
        await _scheduled_match(stage=stage)
        async with client_for(app, raw) as c:
            body = (await c.get('/api/reports/stage-utilization', params=WINDOW)).json()
        assert [r['stage_name'] for r in body['stages']] == ['Main']
        row = body['stages'][0]
        assert 'matches' not in row
        assert row['match_count'] == 1
        assert 'unplaced_candidate_count' in body


class TestPassThroughReports:
    async def test_every_report_answers_over_a_window(self, db, app):
        _, raw = await create_user_token(username='staff', roles=[Role.STAFF])
        await _scheduled_match()
        paths = [
            '/api/reports/match-operations',
            '/api/reports/crew-coverage',
            '/api/reports/tournament-health',
            '/api/reports/crew-participation-trends',
            '/api/reports/activity-trends',
            '/api/reports/volunteer-hour-trends',
        ]
        async with client_for(app, raw) as c:
            for path in paths:
                resp = await c.get(path, params=WINDOW)
                assert resp.status_code == 200, path

    async def test_matches_active_at_finds_a_match_in_progress(self, db, app):
        _, raw = await create_user_token(username='staff', roles=[Role.STAFF])
        match = await _scheduled_match(at=NOW)
        async with client_for(app, raw) as c:
            rows = (await c.get(
                '/api/reports/matches-active-at',
                params={'instant': (NOW + timedelta(minutes=10)).isoformat()},
            )).json()
        assert [row['match_id'] for row in rows] == [match.id]

    async def test_a_backwards_window_is_400(self, db, app):
        _, raw = await create_user_token(username='staff', roles=[Role.STAFF])
        async with client_for(app, raw) as c:
            resp = await c.get('/api/reports/activity-trends', params={
                'start': WINDOW['end'], 'end': WINDOW['start'],
            })
            assert resp.status_code == 400

    async def test_an_unknown_bucket_is_422(self, db, app):
        _, raw = await create_user_token(username='staff', roles=[Role.STAFF])
        async with client_for(app, raw) as c:
            resp = await c.get(
                '/api/reports/activity-trends', params={**WINDOW, 'bucket': 'fortnight'},
            )
            assert resp.status_code == 422

    async def test_volunteer_hour_trends_404s_without_the_volunteers_flag(self, db, app):
        await TenantFeatureFlag.filter(
            tenant_id=1, flag=FeatureFlag.VOLUNTEERS.value,
        ).update(available=False, enabled=False)
        _, raw = await create_user_token(username='staff', roles=[Role.STAFF])
        async with client_for(app, raw) as c:
            assert (await c.get('/api/reports/volunteer-hour-trends', params=WINDOW)).status_code == 404
            assert (await c.get('/api/reports/activity-trends', params=WINDOW)).status_code == 200

    async def test_volunteer_hour_trends_without_the_flag_404s_for_a_non_admin_too(self, db, app):
        """The flag is checked before the role, so a player can't tell a
        switched-off feature from one they lack the role for."""
        await TenantFeatureFlag.filter(
            tenant_id=1, flag=FeatureFlag.VOLUNTEERS.value,
        ).update(available=False, enabled=False)
        _, raw = await create_user_token(username='player')
        async with client_for(app, raw) as c:
            assert (await c.get('/api/reports/volunteer-hour-trends', params=WINDOW)).status_code == 404


class TestTelemetry:
    async def test_staff_reads_the_summary_and_leaderboards(self, db, app):
        user, raw = await create_user_token(username='staff', roles=[Role.STAFF])
        await TelemetryEvent.create(
            category='page', event_type='page_view', path='/home',
            user=user, session_id='s1',
        )
        async with client_for(app, raw) as c:
            summary = (await c.get('/api/telemetry/summary')).json()
            assert summary['total_events'] == 1
            assert summary['page_views'] == 1

            paths = (await c.get('/api/telemetry/top', params={'dimension': 'paths'})).json()
            assert [row['path'] for row in paths] == ['/home']

    async def test_an_unknown_dimension_is_422(self, db, app):
        _, raw = await create_user_token(username='staff', roles=[Role.STAFF])
        async with client_for(app, raw) as c:
            resp = await c.get('/api/telemetry/top', params={'dimension': 'planets'})
            assert resp.status_code == 422

    async def test_a_plain_member_is_forbidden(self, db, app):
        _, raw = await create_user_token(username='plain')
        async with client_for(app, raw) as c:
            assert (await c.get('/api/telemetry/summary')).status_code == 403


class TestTenantIsolation:
    async def test_reports_never_count_another_communitys_rows(self, app, two_tenants):
        tenant_a, tenant_b = two_tenants
        with tenant_scope(tenant_a.id):
            await _scheduled_match(name='A Cup')
        with tenant_scope(tenant_b.id):
            _, token_b = await create_user_token(username='b-staff', roles=[Role.STAFF])

        async with client_for(app, token_b) as c:
            rows = (await c.get('/api/reports/tournament-health', params=WINDOW)).json()
            assert rows == []
            active = (await c.get(
                '/api/reports/matches-active-at', params={'instant': NOW.isoformat()},
            )).json()
            assert active == []

    async def test_telemetry_never_counts_another_communitys_events(self, app, two_tenants):
        tenant_a, tenant_b = two_tenants
        with tenant_scope(tenant_a.id):
            a_user, _ = await create_user_token(username='a-staff', roles=[Role.STAFF])
            await TelemetryEvent.create(
                category='page', event_type='page_view', path='/a', user=a_user,
            )
        with tenant_scope(tenant_b.id):
            _, token_b = await create_user_token(username='b-staff', roles=[Role.STAFF])

        async with client_for(app, token_b) as c:
            assert (await c.get('/api/telemetry/summary')).json()['total_events'] == 0
