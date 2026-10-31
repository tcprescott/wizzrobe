"""REST API cross-tenant isolation.

A personal token belongs to exactly one tenant; ``api/dependencies`` sets the
request tenant context from it. These tests prove a token for tenant A sees only
A's data — both through the repository-backed list endpoints and the direct
load-or-404 reads in the action routers.
"""

from tests.api_helpers import client_for


async def test_matches_list_is_tenant_isolated(two_tenant_api):
    ctx = two_tenant_api
    async with client_for(ctx['app'], ctx['token_a']) as client:
        r = await client.get('/api/matches')
        assert r.status_code == 200
        assert [m['id'] for m in r.json()] == [ctx['ma'].id]
    async with client_for(ctx['app'], ctx['token_b']) as client:
        r = await client.get('/api/matches')
        assert [m['id'] for m in r.json()] == [ctx['mb'].id]


async def test_cannot_read_other_tenant_match_by_id(two_tenant_api):
    ctx = two_tenant_api
    async with client_for(ctx['app'], ctx['token_a']) as client:
        r = await client.get(f"/api/matches/{ctx['mb'].id}")
        assert r.status_code == 404
        r = await client.get(f"/api/matches/{ctx['ma'].id}")
        assert r.status_code == 200


async def test_action_router_load_is_tenant_scoped(two_tenant_api):
    ctx = two_tenant_api
    async with client_for(ctx['app'], ctx['token_a']) as client:
        r = await client.post(f"/api/matches/{ctx['mb'].id}/seat")
        assert r.status_code == 404
