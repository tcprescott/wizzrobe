"""A feature-gated MCP tool can't be used to learn which communities exist.

The feature gate used to run before the membership floor, so a role-less token
calling a gated tool got "not enabled" for a real slug and "No community" for a
made-up one.
"""

from models import Tenant
from tests.mcp.conftest import call_tool, create_oauth_token, mcp_session


async def test_real_and_made_up_slugs_read_the_same(db):
    await Tenant.create(name='Hidden', slug='hidden-community')
    _, raw = await create_oauth_token(username='prober')
    async with mcp_session() as client:
        real_error, real_text = await call_tool(client, raw, 'list_equipment', tenant='hidden-community')
        fake_error, fake_text = await call_tool(client, raw, 'list_equipment', tenant='no-such-place')
    assert real_error and fake_error
    assert 'not enabled' not in real_text
    assert real_text.replace('hidden-community', '<slug>') == fake_text.replace('no-such-place', '<slug>')
