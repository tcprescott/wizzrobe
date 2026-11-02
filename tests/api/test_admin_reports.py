"""/api/admin/reports: the app's Reports screen reads."""

import json
from datetime import datetime, timezone

from models import AuditLog, FeatureFlag, Match, Role, TelemetryEvent, TenantFeatureFlag, Tournament
from tests.api_helpers import client_for, create_user_token

DAY = {'start': '2026-06-01', 'end': '2026-06-01'}


async def test_staff_get_every_report_and_the_overview(db, app):
    _, raw = await create_user_token(username='staff', roles=[Role.STAFF])
    async with client_for(app, raw) as c:
        body = (await c.get('/api/admin/reports')).json()
    assert body['overview'] is True
    keys = [r['key'] for r in body['reports']]
    assert keys[:2] == ['insights', 'capacity'] and {'audit', 'telemetry', 'messages', 'exports'} <= set(keys)
    assert body['jumps'][0]['label'] == 'Whole event'


async def test_a_crew_coordinator_skips_the_staff_only_reports(db, app):
    user, raw = await create_user_token(username='cc')
    tournament = await Tournament.create(name='Cup')
    await tournament.crew_coordinators.add(user)
    async with client_for(app, raw) as c:
        keys = {r['key'] for r in (await c.get('/api/admin/reports')).json()['reports']}
    assert 'capacity' in keys and not keys & {'audit', 'telemetry', 'messages', 'exports'}


async def test_a_volunteer_coordinator_gets_volunteers_only_and_no_overview(db, app):
    _, raw = await create_user_token(username='vc', roles=[Role.VOLUNTEER_COORDINATOR])
    async with client_for(app, raw) as c:
        body = (await c.get('/api/admin/reports')).json()
        assert body['overview'] is False
        assert [r['key'] for r in body['reports']] == ['volunteers', 'exports']
        assert (await c.get('/api/admin/reports/volunteer-coverage', params=DAY)).status_code == 200
        assert (await c.get('/api/admin/reports/overview', params=DAY)).status_code == 403


async def test_a_plain_member_is_refused(db, app):
    _, raw = await create_user_token(username='plain')
    async with client_for(app, raw) as c:
        assert (await c.get('/api/admin/reports')).status_code == 403
        assert (await c.get('/api/admin/reports/capacity', params=DAY)).status_code == 403


async def test_volunteer_coverage_404s_without_the_flag(db, app):
    await TenantFeatureFlag.filter(tenant_id=1, flag=FeatureFlag.VOLUNTEERS.value).update(available=False, enabled=False)
    _, raw = await create_user_token(username='staff', roles=[Role.STAFF])
    async with client_for(app, raw) as c:
        assert (await c.get('/api/admin/reports/volunteer-coverage', params=DAY)).status_code == 404
        keys = {r['key'] for r in (await c.get('/api/admin/reports')).json()['reports']}
    assert 'volunteers' not in keys


async def test_overview_and_capacity_series(db, app):
    _, raw = await create_user_token(username='staff', roles=[Role.STAFF])
    tournament = await Tournament.create(name='Cup', players_per_match=2)
    await Match.create(tournament=tournament, scheduled_at=datetime(2026, 6, 1, 15, 0, tzinfo=timezone.utc))
    async with client_for(app, raw) as c:
        kpis = (await c.get('/api/admin/reports/overview', params=DAY)).json()
        series = (await c.get('/api/admin/reports/capacity', params=DAY)).json()
        bad = await c.get('/api/admin/reports/capacity', params={'start': '2026-06-02', 'end': '2026-06-01'})
    assert [k['key'] for k in kpis] == ['peak_players', 'peak_stages', 'matches', 'stream_coverage']
    assert {k['report'] for k in kpis} == {'capacity', 'stages', 'match_ops', 'crew'}
    assert len(series['times']) == len(series['players']) == len(series['on_stream']) > 1
    assert bad.status_code == 400


async def test_audit_page_describes_rows_and_resolves_names(db, app):
    user, raw = await create_user_token(username='boss', roles=[Role.STAFF])
    await AuditLog.create(
        user=user, action='match.created', details=json.dumps({'match_id': 7}),
        created_at=datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc),
    )
    async with client_for(app, raw) as c:
        resp = await c.get('/api/admin/reports/audit', params={'start': '2000-01-01', 'end': '2100-01-01'})
    body = resp.json()
    assert resp.status_code == 200 and body['total'] >= 1
    row = next(r for r in body['items'] if r['action'] == 'match.created')
    assert row['actor_name'] == 'boss' and row['subject'] == 'Match #7' and row['match_id'] == 7
    assert any(a['key'] == 'matches' for a in body['areas'])


async def test_telemetry_events_page(db, app):
    user, raw = await create_user_token(username='staff', roles=[Role.STAFF])
    await TelemetryEvent.create(category='page', event_type='page_view', path='/home', user=user)
    async with client_for(app, raw) as c:
        body = (await c.get('/api/admin/reports/telemetry-events')).json()
    assert body['total'] == 1 and body['items'][0]['user_name'] == 'staff'


async def test_staff_only_reads_refuse_a_tournament_admin(db, app):
    user, raw = await create_user_token(username='ta')
    tournament = await Tournament.create(name='Cup')
    await tournament.admins.add(user)
    async with client_for(app, raw) as c:
        assert (await c.get('/api/admin/reports/audit', params=DAY)).status_code == 403
        assert (await c.get('/api/admin/reports/telemetry-events')).status_code == 403
        rows = (await c.get('/api/admin/reports/exportable-tournaments')).json()
    assert [r['name'] for r in rows] == ['Cup']
