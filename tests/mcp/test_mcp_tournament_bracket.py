"""MCP ``get_tournament_bracket``: the whole bracket, read as a plain member."""

from models import BracketState, TenantMembership
from tests.mcp.conftest import call_tool, create_oauth_token, mcp_session
from tests.services._bracket_overview_setup import native_elim

TENANT = 'default'


class TestTournamentBracket:
    async def test_a_member_gets_feeders_and_no_accounts(self, db):
        tournament, _, (semi1, semi2, final), _ = await native_elim()
        user, raw = await create_oauth_token(username='bracket-reader')
        await TenantMembership.create(user=user, tenant_id=1)
        async with mcp_session() as client:
            is_error, payload = await call_tool(
                client, raw, 'get_tournament_bracket', tenant=TENANT, tournament_id=tournament.id,
            )
        assert not is_error, payload
        assert payload['source'] == 'native'
        sets = {s['id']: s for s in payload['phases'][0]['pools'][0]['sets']}
        assert sets[final.id]['slots'][1]['from_set_id'] == semi2.id
        assert sets[semi1.id]['winner_to'] == {'set_id': final.id, 'slot': 1}
        assert {e['user_id'] for e in payload['entrants']} == {None}

    async def test_an_unpublished_bracket_is_not_found(self, db):
        tournament, *_ = await native_elim(state=BracketState.DRAFT)
        user, raw = await create_oauth_token(username='bracket-reader')
        await TenantMembership.create(user=user, tenant_id=1)
        async with mcp_session() as client:
            is_error, payload = await call_tool(
                client, raw, 'get_tournament_bracket', tenant=TENANT, tournament_id=tournament.id,
            )
        assert is_error and 'not_found:' in payload, payload
