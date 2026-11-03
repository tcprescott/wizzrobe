"""Wizzrobe's own app (web/) as a first-party client of the /oauth server.

One sign-in, no consent screen, and a grant tied to no community: each REST
call names its community in X-Wizzrobe-Community and is checked for
membership there, exactly like any other token.
"""

from datetime import datetime, timezone
from typing import Optional

import pytest

from application.repositories.tenant_membership_repository import TenantMembershipRepository
from application.services import OAuthAppService, OAuthGrantService
from application.tenant_context import tenant_scope
from models import ApiToken, FeatureFlag, OAuthApp, OAuthClientType, Role, Tenant, TenantFeatureFlag, User
from tests.api_helpers import create_community_member, create_user_token
from tests.conftest import DEFAULT_TEST_TENANT_ID
from tests.oauth.conftest import client, exchange, grant_tokens, make_app, pkce, query, start

pytestmark = pytest.mark.usefixtures('db')

APP_REDIRECT = 'http://localhost:5173/auth/callback'
HEADER = 'X-Wizzrobe-Community'


async def _first_party_app() -> OAuthApp:
    admin, _ = await create_user_token(username='platform', roles=[Role.SUPER_ADMIN])
    return await OAuthAppService().register_first_party(admin, client_id='wizzrobe-app', redirect_uris=[APP_REDIRECT])


async def _sign_in(user: User, app: OAuthApp) -> dict:
    verifier, challenge = pkce()
    async with client() as http:
        txn = await start(http, app, challenge, redirect_uri=APP_REDIRECT)
        redirect = await OAuthGrantService().approve_first_party(txn, user)
        assert redirect is not None and redirect.startswith(APP_REDIRECT)
        resp = await exchange(http, app, None, query(redirect)['code'], verifier, redirect_uri=APP_REDIRECT)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _me(access: str, community: Optional[str]):
    headers = {HEADER: community} if community else {}
    async with client(access) as http:
        return await http.get('/api/users/me', headers=headers)


async def test_register_is_platform_admin_only_and_idempotent():
    member = await create_community_member(username='someone')
    with pytest.raises(PermissionError):
        await OAuthAppService().register_first_party(member, client_id='wizzrobe-app', redirect_uris=[APP_REDIRECT])
    app = await _first_party_app()
    assert app.first_party and app.client_type == OAuthClientType.PUBLIC
    admin = await User.get(username='platform')
    again = await OAuthAppService().register_first_party(
        admin, client_id='wizzrobe-app', redirect_uris=[APP_REDIRECT, 'https://app.example.org/auth/callback'],
    )
    assert again.id == app.id and len(again.redirect_uris) == 2


async def test_third_party_app_is_not_auto_approved():
    owner = await create_community_member(username='dev')
    app, _ = await make_app(owner, client_type=OAuthClientType.PUBLIC)
    _, challenge = pkce()
    async with client() as http:
        txn = await start(http, app, challenge)
    assert await OAuthGrantService().approve_first_party(txn, owner) is None


async def test_one_grant_works_in_each_community_the_person_belongs_to(two_tenants):
    tenant_a, tenant_b = two_tenants
    app = await _first_party_app()
    with tenant_scope(tenant_a.id):
        player = await create_community_member(username='player')
    await TenantMembershipRepository.add(player, tenant_b.id)
    tokens = await _sign_in(player, app)

    grant = await ApiToken.get(token_prefix=tokens['access_token'][:17])
    assert grant.tenant_id is None

    for slug in (tenant_a.slug, tenant_b.slug):
        resp = await _me(tokens['access_token'], slug)
        assert resp.status_code == 200, resp.text
        assert resp.json()['username'] == 'player'


async def test_a_community_you_dont_belong_to_is_refused(two_tenants):
    tenant_a, tenant_b = two_tenants
    app = await _first_party_app()
    with tenant_scope(tenant_a.id):
        player = await create_community_member(username='player')
    tokens = await _sign_in(player, app)
    assert (await _me(tokens['access_token'], tenant_b.slug)).status_code == 403
    assert (await _me(tokens['access_token'], 'no-such-community')).status_code == 404
    assert (await _me(tokens['access_token'], None)).status_code == 400


async def test_not_held_to_the_third_party_flag_or_scopes():
    app = await _first_party_app()
    player = await create_community_member(username='player')
    await TenantFeatureFlag.filter(
        tenant_id=DEFAULT_TEST_TENANT_ID, flag=FeatureFlag.THIRD_PARTY_APPS.value,
    ).update(enabled=False, available=False)
    tokens = await _sign_in(player, app)
    tenant = await Tenant.get(id=DEFAULT_TEST_TENANT_ID)
    assert (await _me(tokens['access_token'], tenant.slug)).status_code == 200
    async with client(tokens['access_token']) as http:
        pat_only = await http.get('/api/tokens', headers={HEADER: tenant.slug})
    assert pat_only.status_code == 200, pat_only.text


async def test_signing_in_again_keeps_the_other_device_signed_in():
    app = await _first_party_app()
    player = await create_community_member(username='player')
    phone = await _sign_in(player, app)
    desktop = await _sign_in(player, app)
    tenant = await Tenant.get(id=DEFAULT_TEST_TENANT_ID)
    assert (await _me(phone['access_token'], tenant.slug)).status_code == 200
    assert (await _me(desktop['access_token'], tenant.slug)).status_code == 200


async def test_refresh_rotates_and_a_disabled_app_stops_working():
    app = await _first_party_app()
    player = await create_community_member(username='player')
    tokens = await _sign_in(player, app)
    async with client() as http:
        resp = await http.post('/oauth/token', data={
            'grant_type': 'refresh_token', 'refresh_token': tokens['refresh_token'], 'client_id': app.client_id,
        })
    assert resp.status_code == 200, resp.text
    fresh = resp.json()
    tenant = await Tenant.get(id=DEFAULT_TEST_TENANT_ID)
    assert (await _me(fresh['access_token'], tenant.slug)).status_code == 200

    await OAuthApp.filter(id=app.id).update(disabled_at=datetime.now(timezone.utc))
    assert (await _me(fresh['access_token'], tenant.slug)).status_code == 401


async def test_my_communities_lists_memberships_only(two_tenants):
    tenant_a, tenant_b = two_tenants
    await Tenant.create(name='Elsewhere', slug='elsewhere')
    app = await _first_party_app()
    with tenant_scope(tenant_a.id):
        player = await create_community_member(username='player')
    await TenantMembershipRepository.add(player, tenant_b.id)
    tokens = await _sign_in(player, app)
    async with client(tokens['access_token']) as http:
        resp = await http.get('/api/me/communities')
    assert resp.status_code == 200, resp.text
    assert sorted(c['slug'] for c in resp.json()) == sorted([tenant_a.slug, tenant_b.slug])


async def test_my_communities_refuses_a_third_party_grant():
    owner = await create_community_member(username='dev')
    app, secret = await make_app(owner)
    async with client() as http:
        tokens = await grant_tokens(http, app, secret, owner, DEFAULT_TEST_TENANT_ID, ['profile'])
    async with client(tokens['access_token']) as http:
        resp = await http.get('/api/me/communities')
    assert resp.status_code == 403


async def test_platform_routes_need_no_community_and_stay_super_admin_only():
    app = await _first_party_app()
    root = await User.get(username='platform')
    member = await create_community_member(username='member')
    root_tokens = await _sign_in(root, app)
    member_tokens = await _sign_in(member, app)
    async with client(root_tokens['access_token']) as http:
        resp = await http.get('/api/platform/communities')
        assert resp.status_code == 200, resp.text
        assert (await http.get('/api/platform/apps')).status_code == 200
        assert (await http.get('/api/me/platform')).json() == {'user_id': root.id, 'super_admin': True}
    async with client(member_tokens['access_token']) as http:
        assert (await http.get('/api/platform/communities')).status_code == 403
        assert (await http.get('/api/me/platform')).json() == {'user_id': member.id, 'super_admin': False}


async def test_passwords_only_through_the_app():
    app = await _first_party_app()
    root = await User.get(username='platform')
    member = await create_community_member(username='discordy')
    tokens = await _sign_in(root, app)
    async with client(tokens['access_token']) as http:
        resp = await http.post('/api/platform/users', json={
            'display_name': 'Venue Laptop', 'login_name': 'venue-laptop', 'password': 'correct horse battery',
        })
        assert resp.status_code == 201, resp.text
        assert resp.json()['login_name'] == 'venue-laptop'
        short = await http.put(f'/api/platform/users/{member.id}/password', json={'login_name': 'discordy', 'password': 'short'})
        assert short.status_code == 400
        resp = await http.put(f'/api/platform/users/{member.id}/password', json={'login_name': 'discordy', 'password': 'long enough password'})
        assert resp.status_code == 200 and resp.json()['login_name'] == 'discordy'
        resp = await http.delete(f'/api/platform/users/{member.id}/password')
        assert resp.status_code == 200 and resp.json()['login_name'] is None
    _, pat = await create_user_token(username='root-pat', roles=[Role.SUPER_ADMIN])
    async with client(pat) as http:
        resp = await http.post('/api/platform/users', json={
            'display_name': 'X', 'login_name': 'x-account', 'password': 'correct horse battery',
        })
    assert resp.status_code == 403
    assert not await User.filter(login_name='x-account').exists()
