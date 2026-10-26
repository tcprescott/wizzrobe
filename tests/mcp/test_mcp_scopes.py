"""Every MCP tool sits in one of the eight scope areas, and a connection only
reaches the areas it was granted.

The catalogue half is the CI check that a tool cannot ship without a scope
decision, or with one that contradicts what the tool does. The connection half
proves the decision is enforced on ``tools/call`` and not only on the listing,
and that connections made before scopes existed keep everything they had.
"""

import pytest

from application.api_scopes import ALL_SCOPES, READ_SCOPES, STAFF_SCOPES, WRITE_SCOPES, ApiScope
from application.repositories.tenant_membership_repository import TenantMembershipRepository
from application.services.mcp_auth_service import grant_scopes, scope_allows_write
from mcpserver.auth import Gate
from mcpserver.registry import TOOL_SPECS, register
from tests.mcp.conftest import _catalogue, call_tool, create_oauth_token, list_tools, mcp_session

TENANT = 'default'


@pytest.fixture(scope='module')
def specs():
    _catalogue()
    return dict(TOOL_SPECS)


def test_every_tool_names_a_scope(specs):
    unscoped = sorted(n for n, s in specs.items() if s.scope is None and s.gate is not Gate.GLOBAL)
    assert unscoped == []
    assert sorted(n for n, s in specs.items() if s.gate is Gate.GLOBAL) == [
        'list_tenants', 'server_version', 'whoami',
    ]


def test_tools_sit_in_the_right_kind_of_area(specs):
    wrong = sorted(
        n for n, s in specs.items()
        if s.scope is not None and s.write != (s.scope in WRITE_SCOPES)
    )
    assert wrong == []


def test_staff_gated_tools_sit_in_staff_areas(specs):
    gated = {Gate.STAFF, Gate.ADMIN, Gate.VOLUNTEER_ADMIN, Gate.PLATFORM}
    wrong = sorted(n for n, s in specs.items() if s.gate in gated and s.scope not in STAFF_SCOPES)
    assert wrong == []


def test_every_read_area_is_used(specs):
    # Volunteer has no MCP tool yet: the shift writes there are coordinator
    # actions (staff:write), and reading your own shifts is profile.
    assert READ_SCOPES <= {s.scope for s in specs.values()}


def test_register_refuses_a_contradictory_scope():
    async def some_write_tool() -> None: ...

    with pytest.raises(ValueError):
        register(None, some_write_tool, gate=Gate.ACTOR, scope=ApiScope.SCHEDULE_READ, write=True)
    with pytest.raises(ValueError):
        register(None, some_write_tool, gate=Gate.ACTOR, scope=ApiScope.PLAY)
    with pytest.raises(ValueError):
        register(None, some_write_tool, gate=Gate.ACTOR, scope=None)
    with pytest.raises(ValueError):
        register(None, some_write_tool, gate=Gate.GLOBAL, scope=ApiScope.PROFILE)
    assert 'some_write_tool' not in TOOL_SPECS


@pytest.mark.parametrize('stored, areas, writes', [
    ('wizzrobe:read', ALL_SCOPES, False),
    ('wizzrobe:read wizzrobe:write', ALL_SCOPES, True),
    ('schedule:read profile', frozenset({ApiScope.SCHEDULE_READ, ApiScope.PROFILE}), False),
    ('schedule:read play', frozenset({ApiScope.SCHEDULE_READ, ApiScope.PLAY}), True),
    ('', frozenset(), False),
])
def test_stored_scope_strings(stored, areas, writes):
    assert grant_scopes(stored) == areas
    assert scope_allows_write(stored) is writes


async def test_a_connection_reaches_only_its_areas(db):
    user, raw = await create_oauth_token(scope='schedule:read')
    await TenantMembershipRepository.add(user, 1)
    async with mcp_session() as client:
        names = {t['name'] for t in await list_tools(client, raw)}
        is_error, _ = await call_tool(client, raw, 'list_tournaments', tenant=TENANT)
        assert not is_error
        is_error, payload = await call_tool(client, raw, 'my_notification_settings', tenant=TENANT)
    assert 'list_tournaments' in names
    assert 'my_notification_settings' not in names
    assert 'whoami' in names
    assert is_error and 'tick it' in str(payload)


async def test_a_legacy_read_connection_keeps_every_read(db, specs):
    _, raw = await create_oauth_token(scope='wizzrobe:read')
    async with mcp_session() as client:
        names = {t['name'] for t in await list_tools(client, raw)}
    expected = {n for n, s in specs.items() if not s.write and s.gate is not Gate.PLATFORM}
    assert expected <= names
    assert not any(specs[n].write for n in names)


async def test_a_read_area_grant_gets_every_read_and_no_writes(db, specs):
    _, raw = await create_oauth_token(scope=' '.join(s.value for s in READ_SCOPES))
    async with mcp_session() as client:
        names = {t['name'] for t in await list_tools(client, raw)}
    assert not any(specs[n].write for n in names)
    reads = {n for n, s in specs.items() if not s.write and s.gate is not Gate.PLATFORM}
    assert reads <= names
