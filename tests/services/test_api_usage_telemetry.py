"""Usage telemetry for the MCP server and REST API.

Every MCP tool call and every authenticated REST request writes one ``api`` row
naming the person, the tool or route template, and the outcome; the ``api`` and
``api_users`` leaderboards and the summary's ``mcp_users`` / ``rest_users`` read
them back for staff.
"""

import json

import pytest

from application.services.telemetry_service import (
    TelemetryCategory,
    TelemetryEventType,
    TelemetryService,
)
from models import Role, TelemetryEvent, User, UserRole
from tests.api_helpers import build_api_app, client_for, create_user_token
from tests.mcp.conftest import call_tool, create_oauth_token, mcp_session

TENANT = 'default'


async def _api_rows() -> list[TelemetryEvent]:
    return await TelemetryEvent.filter(category=TelemetryCategory.API).order_by('id')


class TestMcpCapture:
    async def test_tool_call_records_user_tool_and_community(self, db):
        user, raw = await create_oauth_token(username='caller', roles=[Role.STAFF])
        async with mcp_session() as client:
            is_error, _ = await call_tool(client, raw, 'list_tournaments', tenant=TENANT)
        assert not is_error
        rows = await _api_rows()
        assert len(rows) == 1
        row = rows[0]
        assert row.event_type == TelemetryEventType.MCP_TOOL_CALL
        assert row.target == 'list_tournaments'
        assert row.user_id == user.id
        assert row.tenant_id == 1
        assert json.loads(row.details) == {'outcome': 'ok', 'actor_username': 'caller'}

    async def test_failed_call_records_error_code(self, db):
        _, raw = await create_oauth_token(username='plain')
        async with mcp_session() as client:
            is_error, _ = await call_tool(client, raw, 'telemetry_summary', tenant=TENANT)
        assert is_error
        row = (await _api_rows())[0]
        assert json.loads(row.details)['outcome'] == 'not_found'

    async def test_no_community_tool_is_a_platform_row(self, db):
        _, raw = await create_oauth_token(username='who')
        async with mcp_session() as client:
            await call_tool(client, raw, 'whoami')
        row = (await _api_rows())[0]
        assert row.target == 'whoami'
        assert row.tenant_id is None

    async def test_disabled_records_nothing(self, db, monkeypatch):
        monkeypatch.setenv('TELEMETRY_ENABLED', 'false')
        _, raw = await create_oauth_token(username='quiet')
        async with mcp_session() as client:
            await call_tool(client, raw, 'whoami')
        assert await _api_rows() == []


class TestRestCapture:
    async def test_request_records_route_template_not_filled_path(self, db):
        user, raw = await create_user_token(username='rester', roles=[Role.STAFF])
        async with client_for(build_api_app(), raw) as client:
            resp = await client.get('/api/tournaments/999999')
        assert resp.status_code == 404
        row = (await _api_rows())[0]
        assert row.event_type == TelemetryEventType.REST_REQUEST
        assert row.target == 'GET /api/tournaments/{tournament_id}'
        assert row.user_id == user.id
        assert row.tenant_id == 1
        assert json.loads(row.details)['outcome'] == '404'

    async def test_success_is_ok(self, db):
        _, raw = await create_user_token(username='ok', roles=[Role.STAFF])
        async with client_for(build_api_app(), raw) as client:
            resp = await client.get('/api/tournaments')
        assert resp.status_code == 200
        row = (await _api_rows())[0]
        assert row.target == 'GET /api/tournaments'
        assert json.loads(row.details)['outcome'] == 'ok'

    async def test_unauthenticated_request_is_not_recorded(self, db):
        async with client_for(build_api_app(), None) as client:
            resp = await client.get('/api/tournaments')
        assert resp.status_code == 401
        assert await _api_rows() == []


async def _staff() -> User:
    user = await User.create(discord_id=4242, username='boss')
    await UserRole.create(user=user, role=Role.STAFF)
    return user


async def _call(event_type: str, target: str, user: User, outcome: str = 'ok') -> None:
    await TelemetryService().track_api_call(
        event_type=event_type, target=target, path='/x', user_id=user.id,
        username=user.username, outcome=outcome,
    )


class TestReads:
    async def test_summary_counts_distinct_people_per_surface(self, db):
        staff = await _staff()
        a = await User.create(discord_id=1, username='a')
        b = await User.create(discord_id=2, username='b')
        await _call(TelemetryEventType.MCP_TOOL_CALL, 'whoami', a)
        await _call(TelemetryEventType.MCP_TOOL_CALL, 'get_match', a)
        await _call(TelemetryEventType.MCP_TOOL_CALL, 'get_match', b)
        await _call(TelemetryEventType.REST_REQUEST, 'GET /api/matches', b)
        summary = await TelemetryService().engagement_summary(staff)
        assert summary['mcp_users'] == 2
        assert summary['rest_users'] == 1

    async def test_top_api_calls(self, db):
        staff = await _staff()
        a = await User.create(discord_id=1, username='a')
        b = await User.create(discord_id=2, username='b')
        await _call(TelemetryEventType.MCP_TOOL_CALL, 'get_match', a)
        await _call(TelemetryEventType.MCP_TOOL_CALL, 'get_match', b, outcome='not_found')
        await _call(TelemetryEventType.REST_REQUEST, 'GET /api/matches', b)
        rows = await TelemetryService().top_api_calls(staff)
        assert rows[0] == {
            'surface': 'mcp', 'target': 'get_match', 'calls': 2, 'users': 2, 'errors': 1,
        }
        assert rows[1]['surface'] == 'rest'

    async def test_top_api_users(self, db):
        staff = await _staff()
        a = await User.create(discord_id=1, username='alice')
        await _call(TelemetryEventType.MCP_TOOL_CALL, 'whoami', a)
        await _call(TelemetryEventType.MCP_TOOL_CALL, 'get_match', a)
        await _call(TelemetryEventType.MCP_TOOL_CALL, 'get_match', a)
        rows = await TelemetryService().top_api_users(staff)
        assert len(rows) == 1
        row = rows[0]
        assert (row['user'], row['surface'], row['calls'], row['targets']) == ('alice', 'mcp', 3, 2)
        assert row['last_call'] is not None

    async def test_reads_are_staff_only(self, db):
        plain = await User.create(discord_id=9, username='p')
        with pytest.raises(PermissionError):
            await TelemetryService().top_api_users(plain)
