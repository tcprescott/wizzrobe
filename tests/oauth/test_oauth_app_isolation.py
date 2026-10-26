"""Community walls for third-party app grants.

A grant (an ``ApiToken`` with ``origin='app'``) and the ``OAuthAppCode`` it
came from are bound to the one community picked on consent. Another
community's data, membership or staff never reach through it.
"""

import hashlib

import pytest

from application.services import OAuthGrantService
from application.tenant_context import tenant_scope
from models import ApiToken, OAuthAppBlock, OAuthAppCode, Role, Tournament, UserRole
from tests.api_helpers import create_community_member
from tests.oauth.conftest import authorize_code, client, exchange, grant_tokens, make_app, pkce, start


async def test_a_grant_reads_only_the_community_it_was_approved_for(two_tenants):
    tenant_a, tenant_b = two_tenants
    with tenant_scope(tenant_a.id):
        owner = await create_community_member(username='dev')
        member = await create_community_member(username='both')
        await Tournament.create(name='A Cup')
    with tenant_scope(tenant_b.id):
        from application.repositories.tenant_membership_repository import TenantMembershipRepository
        await TenantMembershipRepository.add(member, tenant_b.id)
        await Tournament.create(name='B Cup')
    app, secret = await make_app(owner)

    async with client() as http:
        tokens = await grant_tokens(http, app, secret, member, tenant_a.id, ['schedule:read'])
    grant = await ApiToken.get(token_hash=hashlib.sha256(tokens['access_token'].encode()).hexdigest())
    code = await OAuthAppCode.get(grant_id=grant.id)
    assert grant.tenant_id == tenant_a.id and code.tenant_id == tenant_a.id

    async with client(tokens['access_token']) as api:
        resp = await api.get('/api/tournaments')
    assert resp.status_code == 200
    names = {t['name'] for t in resp.json()}
    assert 'A Cup' in names and 'B Cup' not in names


async def test_consent_offers_only_communities_the_person_belongs_to(two_tenants):
    tenant_a, tenant_b = two_tenants
    with tenant_scope(tenant_a.id):
        owner = await create_community_member(username='dev')
        member = await create_community_member(username='only-a')
    app, _ = await make_app(owner)
    _, challenge = pkce()
    async with client() as http:
        txn = await start(http, app, challenge)
    view = await OAuthGrantService().consent_view(txn, member)
    assert [c.tenant_id for c in view.communities] == [tenant_a.id]
    with pytest.raises(ValueError):
        await OAuthGrantService().approve(txn, member, tenant_b.id, ['profile'])
    assert not await OAuthAppCode.filter(tenant_id=tenant_b.id).exists()


async def test_staff_elsewhere_does_not_unlock_staff_scopes_here(two_tenants):
    tenant_a, tenant_b = two_tenants
    with tenant_scope(tenant_a.id):
        owner = await create_community_member(username='dev')
        member = await create_community_member(username='player-here')
    with tenant_scope(tenant_b.id):
        from application.repositories.tenant_membership_repository import TenantMembershipRepository
        await TenantMembershipRepository.add(member, tenant_b.id)
        await UserRole.create(user=member, role=Role.STAFF, tenant_id=tenant_b.id)
    app, _ = await make_app(owner)
    _, challenge = pkce()
    async with client() as http:
        txn = await start(http, app, challenge)
    view = await OAuthGrantService().consent_view(txn, member)
    offers = {c.tenant_id: {s.value for s in c.scopes} for c in view.communities}
    assert 'staff:read' not in offers[tenant_a.id]
    assert 'staff:read' in offers[tenant_b.id]
    with pytest.raises(ValueError):
        await OAuthGrantService().approve(txn, member, tenant_a.id, ['staff:read'])


async def test_removal_from_the_community_revokes_its_grants_only(two_tenants):
    tenant_a, tenant_b = two_tenants
    with tenant_scope(tenant_a.id):
        owner = await create_community_member(username='dev')
        member = await create_community_member(username='both')
    from application.repositories.tenant_membership_repository import TenantMembershipRepository
    await TenantMembershipRepository.add(member, tenant_b.id)
    app, secret = await make_app(owner)
    async with client() as http:
        a = await grant_tokens(http, app, secret, member, tenant_a.id, ['profile'])
        b = await grant_tokens(http, app, secret, member, tenant_b.id, ['profile'])

    from datetime import datetime, timezone

    from application.repositories.api_token_repository import ApiTokenRepository
    await ApiTokenRepository.revoke_for_user_in_tenant(member.id, tenant_b.id, datetime.now(timezone.utc))
    async with client(a['access_token']) as api:
        assert (await api.get('/api/users/me')).status_code == 200
    async with client(b['access_token']) as api:
        assert (await api.get('/api/users/me')).status_code == 401


async def test_a_code_cannot_be_redeemed_once_the_member_left_its_community(two_tenants):
    tenant_a, _ = two_tenants
    with tenant_scope(tenant_a.id):
        owner = await create_community_member(username='dev')
        member = await create_community_member(username='leaver')
    app, secret = await make_app(owner)
    verifier, challenge = pkce()
    async with client() as http:
        code = await authorize_code(http, app, member, tenant_a.id, ['profile'], challenge)
        from models import TenantMembership
        await TenantMembership.filter(user_id=member.id, tenant_id=tenant_a.id).delete()
        resp = await exchange(http, app, secret, code, verifier)
    assert resp.json()['error'] == 'invalid_grant'


async def test_a_block_in_one_community_leaves_the_other_alone(two_tenants):
    """OAuthAppBlock is per community: staff of A blocking an app ends A's grants,
    hides A on consent and refuses A's tokens, while B's grant keeps working and
    B's staff never see A's block."""
    tenant_a, tenant_b = two_tenants
    with tenant_scope(tenant_a.id):
        owner = await create_community_member(username='dev')
        staff_a = await create_community_member(username='staff-a')
        await UserRole.create(user=staff_a, role=Role.STAFF, tenant_id=tenant_a.id)
        member = await create_community_member(username='both')
    with tenant_scope(tenant_b.id):
        from application.repositories.tenant_membership_repository import TenantMembershipRepository
        await TenantMembershipRepository.add(member, tenant_b.id)
        staff_b = await create_community_member(username='staff-b')
        await UserRole.create(user=staff_b, role=Role.STAFF, tenant_id=tenant_b.id)
    app, secret = await make_app(owner)

    async with client() as http:
        tokens_a = await grant_tokens(http, app, secret, member, tenant_a.id, ['schedule:read'])
        tokens_b = await grant_tokens(http, app, secret, member, tenant_b.id, ['schedule:read'])

    service = OAuthGrantService()
    with tenant_scope(tenant_a.id):
        assert await service.block_app_here(staff_a, app.id) == 1
        rows_a = await service.list_community_apps(staff_a)
    assert [(r.app_id, r.blocked, r.members) for r in rows_a] == [(app.id, True, 0)]
    with tenant_scope(tenant_b.id):
        rows_b = await service.list_community_apps(staff_b)
    assert [(r.app_id, r.blocked, r.members) for r in rows_b] == [(app.id, False, 1)]
    assert await OAuthAppBlock.filter(tenant_id=tenant_b.id).count() == 0

    async with client(tokens_a['access_token']) as api:
        assert (await api.get('/api/tournaments')).status_code == 401
    async with client(tokens_b['access_token']) as api:
        assert (await api.get('/api/tournaments')).status_code == 200

    _, challenge = pkce()
    async with client() as http:
        txn = await start(http, app, challenge)
    view = await service.consent_view(txn, member)
    assert [c.tenant_id for c in view.communities] == [tenant_b.id]
    with pytest.raises(ValueError):
        await service.approve(txn, member, tenant_a.id, ['profile'])
