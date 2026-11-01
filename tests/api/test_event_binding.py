"""REST calls work in one event: ``?event=`` picks it, a row's own event wins.

The static half keeps every event-owned router honest: each attaches
``event_binding`` and maps every path parameter that names a row. The rest
drives the real app with a match in each of two events.
"""

from datetime import datetime, timezone

import pytest
from fastapi.routing import APIRoute

from application.event_context import event_scope
from application.event_writes import event_id_path
from models import AuditLog, Event, EventStatus, Match, Role, SentMessage, TelemetryEvent, Tournament
from tests.api_helpers import client_for, create_user_token

EVENT_OWNED_PREFIXES = (
    '/api/async-qualifiers', '/api/brackets', '/api/check-in', '/api/crew', '/api/discord-events',
    '/api/matches', '/api/race-rooms', '/api/reports', '/api/seeds', '/api/speedgaming',
    '/api/stages', '/api/tournaments', '/api/triforce-texts', '/api/volunteers',
    '/api/reschedule-requests', '/api/me/home', '/api/me/schedule', '/api/me/bookable',
)
NOT_ROWS = {
    'user_id': 'a person, community-wide',
    'role': 'a crew role name',
    'crew_type': 'commentator or tracker; the crew id is resolved by the service',
    'crew_id': 'paired with crew_type, so no single model',
    'task_id': 'a seed-roll task id from the randomizer',
    'stage_key': 'a bracket stage key, not a Stage row',
    'source': 'a bracket site name',
    'key': "a matchup id within its source's own table, checked against the caller's own list",
}


def _binding(route: APIRoute):
    stack = list(route.dependant.dependencies)
    while stack:
        dep = stack.pop()
        if hasattr(dep.call, 'event_rows'):
            return dep.call
        stack.extend(dep.dependencies)
    return None


def _api_routes():
    import api

    return [r for r in api.router.routes if isinstance(r, APIRoute)]


def test_every_event_owned_route_binds_its_event():
    missing = [
        f'{sorted(r.methods)} {r.path}' for r in _api_routes()
        if r.path.startswith(EVENT_OWNED_PREFIXES) and _binding(r) is None
    ]
    assert not missing, f'add event_binding(...) to these routers: {missing}'


def test_every_row_parameter_is_mapped_to_an_event_owned_model():
    problems = []
    for route in _api_routes():
        binding = _binding(route)
        if binding is None:
            continue
        rows = binding.event_rows
        for name in route.param_convertors:
            if name not in rows and name not in NOT_ROWS:
                problems.append(f'{route.path}: {name} is neither mapped nor in NOT_ROWS')
        for name, model in rows.items():
            if event_id_path(model) is None:
                problems.append(f'{route.path}: {name} maps to {model.__name__}, which no event owns')
    assert not problems, problems


@pytest.fixture
async def two_events(db):
    other = await Event.create(tenant_id=1, name='SGL 2027', slug='sgl27', status=EventStatus.LIVE, is_default=False)
    draft = await Event.create(tenant_id=1, name='SGL 2028', slug='sgl28', status=EventStatus.DRAFT, is_default=False)
    when = datetime(2026, 10, 23, 18, tzinfo=timezone.utc)
    t_a = await Tournament.create(name='Cup A')
    m_a = await Match.create(tournament=t_a, scheduled_at=when)
    with event_scope(other.id):
        t_b = await Tournament.create(name='Cup B')
        m_b = await Match.create(tournament=t_b, scheduled_at=when)
    return {'a': m_a, 'b': m_b, 'other': other, 'draft': draft}


async def _member_client(app, **kwargs):
    _, token = await create_user_token(username='caller', **kwargs)
    return client_for(app, token)


async def test_list_defaults_to_the_default_event_and_follows_the_slug(app, two_events):
    async with await _member_client(app) as client:
        default = (await client.get('/api/matches')).json()
        other = (await client.get('/api/matches', params={'event': 'sgl27'})).json()
    assert [m['id'] for m in default] == [two_events['a'].id]
    assert [m['id'] for m in other] == [two_events['b'].id]


async def test_a_row_call_works_in_the_rows_event(app, two_events):
    b = two_events['b']
    async with await _member_client(app, roles=[Role.STAFF]) as client:
        plain = await client.get(f'/api/matches/{b.id}/crew')
        agreeing = await client.get(f'/api/matches/{b.id}/crew', params={'event': 'sgl27'})
        contradicting = await client.get(f'/api/matches/{b.id}/crew', params={'event': 'default'})
    assert plain.status_code == 200
    assert agreeing.status_code == 200
    assert contradicting.status_code == 404


async def test_unknown_slug_is_a_404(app, two_events):
    async with await _member_client(app) as client:
        response = await client.get('/api/matches', params={'event': 'nope'})
    assert response.status_code == 404


async def test_a_draft_is_hidden_from_players_and_open_to_staff(app, two_events):
    async with await _member_client(app) as client:
        player = await client.get('/api/matches', params={'event': 'sgl28'})
    _, token = await create_user_token(username='boss', roles=[Role.STAFF])
    async with client_for(app, token) as client:
        staff = await client.get('/api/matches', params={'event': 'sgl28'})
    assert player.status_code == 404
    assert staff.status_code == 200


async def test_events_are_listed_per_viewer_and_run_by_staff(app, two_events):
    async with await _member_client(app) as client:
        seen = [e['slug'] for e in (await client.get('/api/events')).json()]
        refused = await client.post('/api/events/sgl28/go-live')
        hidden = await client.get('/api/events/sgl28')
    assert seen == ['default', 'sgl27']
    assert refused.status_code == 404
    assert hidden.status_code == 404

    _, token = await create_user_token(username='boss', roles=[Role.STAFF])
    async with client_for(app, token) as client:
        created = await client.post('/api/events', json={'name': 'SGL 2029', 'copy_from': 'default', 'copy': ['stages']})
        live = await client.post('/api/events/sgl28/go-live')
        settings = await client.put('/api/events/sgl27/settings/broadcasters', json={'value': 'true'})
        listed = await client.get('/api/events/sgl27/settings')
        wrong_key = await client.put('/api/events/sgl27/settings/discord_guild_id', json={'value': '1'})
        deleted = await client.delete(f"/api/events/{created.json()['event']['slug']}")
    assert created.status_code == 201
    assert created.json()['event']['status'] == 'draft'
    assert live.json()['status'] == 'live'
    assert settings.status_code == 200
    assert {'name': 'broadcasters', 'value': 'true'} in listed.json()
    assert wrong_key.status_code == 400
    assert deleted.status_code == 204


async def test_community_reports_filter_by_event_only_when_asked(app, two_events):
    other = two_events['other']
    await AuditLog.create(tenant_id=1, action='match.created')
    await AuditLog.create(tenant_id=1, event_id=other.id, action='match.created')
    await SentMessage.create(tenant_id=1, discord_id='1', body='hi')
    await SentMessage.create(tenant_id=1, event_id=other.id, discord_id='1', body='hi')
    await TelemetryEvent.create(tenant_id=1, category='page', event_type='page_view', path='/a')
    await TelemetryEvent.create(tenant_id=1, event_id=other.id, category='page', event_type='page_view', path='/b')

    _, token = await create_user_token(username='boss', roles=[Role.STAFF])
    async with client_for(app, token) as client:
        audit_all = (await client.get('/api/audit-logs')).json()['total']
        audit_one = (await client.get('/api/audit-logs', params={'event': 'sgl27'})).json()['total']
        sent_all = (await client.get('/api/sent-messages')).json()['total']
        sent_one = (await client.get('/api/sent-messages', params={'event': 'sgl27'})).json()
        paths = (await client.get('/api/telemetry/top', params={'dimension': 'paths', 'event': 'sgl27'})).json()
        unknown = await client.get('/api/audit-logs', params={'event': 'nope'})
    assert audit_one == 1 and audit_all > audit_one
    assert (sent_all, sent_one['total'], sent_one['counts']['sent']) == (2, 1, 1)
    assert [row['path'] for row in paths] == ['/b']
    assert unknown.status_code == 404
