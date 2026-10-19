"""get_member_details: the member sheet's MCP twin, staff only."""

from models import Role
from tests.api_helpers import create_community_member
from tests.mcp.conftest import call_tool, create_oauth_token, mcp_session

TENANT = 'default'


async def _staff():
    return await create_oauth_token(username='mcp-reader', roles=[Role.STAFF])


class TestMemberDetails:
    async def test_staff_see_a_members_settings_and_discord_id_as_a_string(self, db):
        _, raw = await _staff()
        member = await create_community_member(username='aerinon', discord_id=320395696098377729)
        async with mcp_session() as client:
            is_error, payload = await call_tool(
                client, raw, 'get_member_details', tenant=TENANT, user_id=member.id,
            )
        assert not is_error, payload
        assert payload['discord_id'] == '320395696098377729'
        assert payload['notifications'][0]['effective'] in {'off', 'silent', 'ping'}

    async def test_a_proctor_is_refused(self, db):
        _, raw = await create_oauth_token(username='mcp-proctor', roles=[Role.PROCTOR])
        member = await create_community_member(username='fynn')
        async with mcp_session() as client:
            is_error, payload = await call_tool(
                client, raw, 'get_member_details', tenant=TENANT, user_id=member.id,
            )
        assert is_error and 'forbidden:' in payload
