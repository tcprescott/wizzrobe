"""MCP calls work in one event: ``event`` picks it, a row's own event wins.

Also the event tools themselves (list, create, go live, settings) and the
events ``whoami`` reports.
"""

from datetime import datetime, timezone

import pytest

from application.event_context import event_scope
from application.repositories import TenantMembershipRepository
from mcpserver.registry import ARG_ROWS, TOOL_SPECS
from models import AuditLog, Event, EventStatus, Match, Role, TelemetryEvent, Tournament
from tests.mcp.conftest import call_tool, create_oauth_token, list_tools, mcp_session

TENANT = 'default'
WHEN = datetime(2026, 10, 23, 18, tzinfo=timezone.utc)


async def test_every_tool_about_an_event_owned_row_is_event_owned(db):
    _, raw = await create_oauth_token(roles=[Role.SUPER_ADMIN], write=True)
    async with mcp_session() as client:
        tools = await list_tools(client, raw)
    loose = [
        t['name'] for t in tools
        if TOOL_SPECS[t['name']].event_rows is None
        and set((t.get('inputSchema') or {}).get('properties') or {}) & set(ARG_ROWS)
    ]
    assert not loose, f'register these with event=True: {loose}'


@pytest.fixture
async def events(db):
    other = await Event.create(tenant_id=1, name='SGL 2027', slug='sgl27', status=EventStatus.LIVE, is_default=False)
    await Event.create(tenant_id=1, name='SGL 2028', slug='sgl28', status=EventStatus.DRAFT, is_default=False)
    t_a = await Tournament.create(name='Cup A')
    m_a = await Match.create(tournament=t_a, scheduled_at=WHEN)
    with event_scope(other.id):
        t_b = await Tournament.create(name='Cup B')
        m_b = await Match.create(tournament=t_b, scheduled_at=WHEN)
    return {'a': m_a, 'b': m_b, 't_b': t_b}


async def test_event_owned_tools_take_an_event_and_community_tools_dont(db):
    _, raw = await create_oauth_token(roles=[Role.STAFF])
    async with mcp_session() as client:
        tools = {t['name']: t for t in await list_tools(client, raw)}
    assert 'event' in tools['get_match']['inputSchema']['properties']
    assert 'event' in tools['list_matches']['inputSchema']['properties']
    assert 'event' not in tools['list_users']['inputSchema']['properties']


async def test_lists_follow_the_event_and_rows_keep_their_own(events):
    _, raw = await create_oauth_token(roles=[Role.STAFF])
    async with mcp_session() as client:
        _, default = await call_tool(client, raw, 'list_matches', tenant=TENANT)
        _, other = await call_tool(client, raw, 'list_matches', tenant=TENANT, event='sgl27')
        _, by_tournament = await call_tool(client, raw, 'list_matches', tenant=TENANT, tournament_id=[events['t_b'].id])
        plain_error, _ = await call_tool(client, raw, 'get_match', tenant=TENANT, match_id=events['b'].id)
        wrong_error, wrong = await call_tool(
            client, raw, 'get_match', tenant=TENANT, match_id=events['b'].id, event='default',
        )
    assert [m['id'] for m in default['result']] == [events['a'].id]
    assert [m['id'] for m in other['result']] == [events['b'].id]
    assert [m['id'] for m in by_tournament['result']] == [events['b'].id]
    assert not plain_error
    assert wrong_error and 'not_found:' in wrong


async def test_a_draft_is_not_found_for_a_player(events):
    player, raw = await create_oauth_token(username='player')
    await TenantMembershipRepository.add(player, 1)
    async with mcp_session() as client:
        is_error, payload = await call_tool(client, raw, 'list_matches', tenant=TENANT, event='sgl28')
        _, listed = await call_tool(client, raw, 'list_events', tenant=TENANT)
    assert is_error and 'not_found:' in payload
    assert [e['slug'] for e in listed['result']] == ['default', 'sgl27']


async def test_whoami_lists_the_events(events):
    _, raw = await create_oauth_token(roles=[Role.STAFF])
    async with mcp_session() as client:
        _, me = await call_tool(client, raw, 'whoami')
    community = next(t for t in me['tenants'] if t['slug'] == TENANT)
    assert [e['slug'] for e in community['events']] == ['default', 'sgl27', 'sgl28']
    assert community['events'][0]['is_default'] is True


async def test_staff_run_an_events_lifecycle(events):
    _, raw = await create_oauth_token(roles=[Role.STAFF], write=True)
    async with mcp_session() as client:
        error, created = await call_tool(client, raw, 'create_event', tenant=TENANT, name='SGL 2029')
        assert not error, created
        slug = created['event']['slug']
        error, live = await call_tool(client, raw, 'go_live_event', tenant=TENANT, slug=slug)
        assert not error and live['status'] == 'live', live
        error, setting = await call_tool(
            client, raw, 'update_event_settings', tenant=TENANT, slug=slug, key='broadcasters', value='true',
        )
        assert not error, setting
        _, settings = await call_tool(client, raw, 'get_event_settings', tenant=TENANT, slug=slug)
        error, payload = await call_tool(client, raw, 'delete_draft_event', tenant=TENANT, slug='sgl28')
    assert {'name': 'broadcasters', 'value': 'true'} in settings['result']
    assert not error, payload
    assert not await Event.filter(slug='sgl28').exists()


async def test_community_reports_take_an_event_filter(events):
    other = await Event.get(slug='sgl27')
    await AuditLog.create(tenant_id=1, action='match.created')
    await AuditLog.create(tenant_id=1, event_id=other.id, action='match.created')
    await TelemetryEvent.create(tenant_id=1, category='page', event_type='page_view', path='/a')
    await TelemetryEvent.create(tenant_id=1, event_id=other.id, category='page', event_type='page_view', path='/b')
    _, raw = await create_oauth_token(roles=[Role.STAFF])
    async with mcp_session() as client:
        _, audit = await call_tool(client, raw, 'list_audit_log', tenant=TENANT, event='sgl27')
        _, summary = await call_tool(client, raw, 'telemetry_summary', tenant=TENANT, event='sgl27')
        _, everything = await call_tool(client, raw, 'telemetry_summary', tenant=TENANT)
    assert audit['total'] == 1
    assert summary['result']['page_views'] == 1
    assert everything['result']['page_views'] == 2
