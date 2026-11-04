"""MCP reads of the public preset library: references, odds, and what stays hidden."""

from application.tenant_context import tenant_scope
from models import MembershipSource, Preset, Role, Tenant, TenantMembership
from tests.mcp.conftest import call_tool, create_oauth_token, mcp_session

TENANT = 'default'


async def test_library_presets_list_by_reference_and_read_with_odds(db):
    _, raw = await create_oauth_token(username='mcp-reader', roles=[Role.STAFF])
    await Preset.create(name='Open Standard', randomizer='alttpr', settings={'glitches': 'none'})
    mystery = await Preset.create(
        name='Weekly Chaos', randomizer='alttprmystery',
        settings={'world_state': {'open': 3, 'inverted': 1}, 'glitches_required': {'none': 1}},
    )

    async with mcp_session() as client:
        _, listing = await call_tool(
            client, raw, 'list_library_presets', tenant=TENANT, search='weekly chaos',
        )
        is_error, detail = await call_tool(
            client, raw, 'get_library_preset', tenant=TENANT, preset_id=mystery.id,
        )
    assert [p['reference'] for p in listing['result']] == ['default/Weekly Chaos']
    assert not is_error, detail
    odds = {row['setting']: {o['value']: o['percent'] for o in row['options']} for row in detail['odds']}
    assert odds['world_state'] == {'open': 75.0, 'inverted': 25.0}


async def test_another_communitys_unlisted_preset_stays_hidden(db):
    user, raw = await create_oauth_token(username='mcp-player')
    await TenantMembership.create(user=user, tenant_id=1, source=MembershipSource.STAFF)
    other = await Tenant.create(slug='elsewhere', name='Elsewhere')
    with tenant_scope(other.id):
        await Preset.create(name='shared', randomizer='alttpr', settings={}, listed=True)
        hidden = await Preset.create(name='secret', randomizer='alttpr', settings={}, listed=False)

    async with mcp_session() as client:
        _, listing = await call_tool(client, raw, 'list_library_presets', tenant=TENANT, search='elsewhere')
        is_error, _ = await call_tool(client, raw, 'get_library_preset', tenant=TENANT, preset_id=hidden.id)
    assert [p['reference'] for p in listing['result']] == ['elsewhere/shared']
    assert is_error
