"""Cross-tenant isolation for Patreon.

A PatreonCampaign row holds a community's creator token, so another community
must never read it. A token sealed for one community must not open after being
copied onto another community's row. Tier labels, patron standing and pending
sign-ins stay inside the community they belong to.
"""

from datetime import datetime, timedelta, timezone

from application.repositories import PatreonRepository
from application.services import _patreon_oauth as oauth
from application.tenant_context import tenant_scope
from models import (
    PatreonCampaign,
    PatreonMembership,
    PatreonOAuthPurpose,
    PatreonOAuthState,
    PatreonTierBadge,
    User,
)


async def _campaign(tenant_id: int, token: str) -> PatreonCampaign:
    return await PatreonCampaign.create(
        tenant_id=tenant_id, campaign_id=f'c-{tenant_id}', campaign_name=f'Campaign {tenant_id}',
        access_token=oauth.seal_for(tenant_id, f'access-{tenant_id}'),
        refresh_token=oauth.seal_for(tenant_id, f'refresh-{tenant_id}'),
        webhook_secret=oauth.seal_for(tenant_id, 'whsec', oauth.WEBHOOK_PURPOSE),
        webhook_token=token,
    )


async def test_patreon_reads_are_isolated(two_tenants):
    a, b = two_tenants
    ca = await _campaign(a.id, 'tok-a')
    cb = await _campaign(b.id, 'tok-b')
    user = await User.create(discord_id=99001, username='patron', patreon_user_id='p-9')
    now = datetime.now(timezone.utc)
    await PatreonTierBadge.create(tenant_id=a.id, tier_id='t-a', tier_title='A tier')
    await PatreonTierBadge.create(tenant_id=b.id, tier_id='t-b', tier_title='B tier')
    await PatreonMembership.create(
        tenant_id=a.id, user_id=user.id, patron_status='active_patron', tier_ids=['t-a'], synced_at=now,
    )
    await PatreonOAuthState.create(
        tenant_id=a.id, state_hash='h-a', purpose=PatreonOAuthPurpose.MEMBER, user_id=user.id,
        expires_at=now + timedelta(minutes=10),
    )

    repo = PatreonRepository()
    with tenant_scope(a.id):
        assert (await repo.get_campaign()).id == ca.id
        assert [t.tier_id for t in await repo.list_tiers()] == ['t-a']
        assert (await repo.get_membership(user.id)) is not None
        assert await repo.membership_counts() == (1, 1)
    with tenant_scope(b.id):
        assert (await repo.get_campaign()).id == cb.id
        assert [t.tier_id for t in await repo.list_tiers()] == ['t-b']
        assert await repo.get_membership(user.id) is None
        assert await repo.active_memberships() == []
        assert await repo.membership_counts() == (0, 0)
        await repo.delete_community_data()
    assert await PatreonCampaign.filter(tenant_id=a.id).exists()
    assert await PatreonMembership.filter(tenant_id=a.id).exists()
    assert await PatreonTierBadge.filter(tenant_id=a.id).exists()
    assert (await repo.get_campaign_by_webhook_token('tok-a')).tenant_id == a.id
    state = await repo.pop_state('h-a')
    assert state is not None and state.tenant_id == a.id


async def test_a_token_copied_to_another_community_does_not_open(two_tenants):
    a, b = two_tenants
    ca = await _campaign(a.id, 'tok-a')
    cb = await _campaign(b.id, 'tok-b')
    assert oauth.open_for(ca, ca.access_token) == f'access-{a.id}'
    cb.access_token = ca.access_token
    assert oauth.open_for(cb, cb.access_token) is None
