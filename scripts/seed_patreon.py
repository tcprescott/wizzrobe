"""Dev seed for Patreon patron badges.

A connected mock campaign (run with ``MOCK_PATREON=1`` so sync and the webhook
talk to the canned campaign, or reconnect a real one on Settings →
Integrations), its two tiers with the Hero tier labelled "Hero patron", and
two linked members: ``player_one`` an active Hero patron and ``player_two`` a
declined one. So the Patreon card, the badge on Admin → Members and the member
sheet, and Me → Accounts all have something to show.
"""

from datetime import datetime, timedelta, timezone

from application.services._patreon_oauth import TOKEN_PURPOSE, WEBHOOK_PURPOSE
from application.utils.mocks.mock_patreon import MOCK_CAMPAIGN_ID, MOCK_LINKED_ACCOUNTS, MOCK_WEBHOOK_SECRET
from application.utils.secret_box import seal
from models import PatreonCampaign, PatreonMembership, PatreonTierBadge, Tenant, User

_TIERS = [('t-hero', 'Hero', 'Hero patron', 0), ('t-supporter', 'Supporter', None, 1)]
_STATUS = {'player_one': ('active_patron', ['t-hero']), 'player_two': ('declined_patron', ['t-supporter'])}


async def seed_patreon_users(users: dict[str, User]) -> None:
    """Link the seed players to the mock campaign's Patreon ids (accounts are global)."""
    for name, patreon_id in MOCK_LINKED_ACCOUNTS.items():
        user = users.get(name)
        if user is None or user.patreon_user_id == patreon_id:
            continue
        await User.filter(id=user.id).update(
            patreon_user_id=patreon_id, patreon_username=f'{user.username} on Patreon',
            patreon_linked_at=datetime.now(timezone.utc),
        )


async def seed_patreon_for_tenant(tenant: Tenant, users: dict[str, User], staff: User) -> None:
    """Idempotent: the campaign by tenant, tiers by id, memberships by (tenant, user)."""
    context = str(tenant.id)
    now = datetime.now(timezone.utc)
    await PatreonCampaign.get_or_create(
        tenant=tenant,
        defaults={
            'campaign_id': MOCK_CAMPAIGN_ID, 'campaign_name': 'Mock Campaign',
            'access_token': seal('mock-access-seed', purpose=TOKEN_PURPOSE, context=context),
            'refresh_token': seal('mock-refresh', purpose=TOKEN_PURPOSE, context=context),
            'token_expires_at': now + timedelta(days=30), 'webhook_id': 'mock-webhook',
            'webhook_secret': seal(MOCK_WEBHOOK_SECRET, purpose=WEBHOOK_PURPOSE, context=context),
            'webhook_token': f'seed-patreon-{tenant.slug}', 'connected_by': staff, 'last_synced_at': now,
        },
    )
    for tier_id, title, label, sort in _TIERS:
        await PatreonTierBadge.get_or_create(
            tenant=tenant, tier_id=tier_id, defaults={'tier_title': title, 'badge_label': label, 'sort': sort},
        )
    for name, (status, tiers) in _STATUS.items():
        user = users.get(name)
        if user is None:
            continue
        await PatreonMembership.get_or_create(
            tenant=tenant, user=user,
            defaults={'patreon_member_id': f'seed-{name}', 'patron_status': status, 'tier_ids': tiers, 'synced_at': now},
        )
