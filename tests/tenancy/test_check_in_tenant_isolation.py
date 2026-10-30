"""Check-in events, their rosters and their badges never cross communities.

Two communities can run check-in for the same Matcherino venue (a shared
event), and the same person can be on both rosters; each community's desk must
see only its own, down to the badge types and badges behind each row.
"""

import pytest

from application.repositories import CheckInPassRepository, CheckInTierRepository
from application.services.check_in_service import CheckInService
from application.tenant_context import tenant_scope
from application.utils.clients.matcherino_client import MockMatcherinoClient, mock_purchase, mock_tier
from models import CheckInEntrant, CheckInEvent, CheckInPass, CheckInTier, MatcherinoLogin, Role, UserRole
from tests.factories import make_user

VENUE = 182105
TIER = mock_tier(1988, 'Base', 8000, qty_sold=1)


def _client() -> MockMatcherinoClient:
    return MockMatcherinoClient(
        [mock_purchase(100, 'Player', tier=TIER, auth_provider='discord', auth_id='801')], [TIER],
    )


async def test_events_rosters_and_badges_do_not_leak_across_tenants(two_tenants):
    a, b = two_tenants
    staff = await make_user(discord_id=800, username='both-staff')
    player = await make_user(discord_id=801, username='player')
    for tenant in (a, b):
        await UserRole.create(user=staff, role=Role.STAFF, tenant=tenant)

    service = CheckInService(client=_client())
    with tenant_scope(a.id):
        event_a = await service.create_event(staff, 'A event', venue_id=VENUE)
        await service.sync_event(staff, event_a.id)
        entrant_a = (await service.roster(event_a))[0]

    with tenant_scope(b.id):
        event_b = await service.create_event(staff, 'B event', venue_id=VENUE)
        assert [e.id for e in await service.list_events()] == [event_b.id]
        assert await service.get_event(event_a.id) is None
        assert await service.get_entrant(entrant_a.id) is None
        assert await CheckInTierRepository.by_pass_id(event_a) == {}
        assert await CheckInPassRepository.by_purchase_id(event_a) == {}
        with pytest.raises(ValueError):
            await service.check_in(staff, entrant_a.id)
        with pytest.raises(ValueError):
            await service.sync_event(staff, event_a.id)
        await service.sync_event(staff, event_b.id)
        entrant_b = (await service.roster(event_b))[0]
        assert [t.title for t in await service.tiers_for(event_b)] == ['Base']

    assert entrant_a.id != entrant_b.id
    assert entrant_a.user_id == entrant_b.user_id == player.id
    await entrant_a.refresh_from_db()
    assert entrant_a.checked_in_at is None
    assert await CheckInEvent.all().count() == 2
    assert await CheckInEntrant.filter(tenant_id=a.id).count() == 1
    for model in (CheckInTier, CheckInPass):
        assert await model.filter(tenant_id=a.id).count() == 1
        assert await model.filter(tenant_id=b.id).count() == 1
    badge_b = await CheckInPass.get(tenant_id=b.id)
    assert badge_b.entrant_id == entrant_b.id


async def test_the_worker_scan_is_cross_tenant_but_each_sync_stays_home(two_tenants, monkeypatch):
    from application.services import check_in_sync_worker
    from models import CheckInEventStatus

    a, b = two_tenants
    staff = await make_user(discord_id=810, username='s')
    for tenant in (a, b):
        await UserRole.create(user=staff, role=Role.STAFF, tenant=tenant)
        await MatcherinoLogin.create(tenant=tenant, refresh_token=f'login-{tenant.slug}')
    signed_in_as = []

    def _client_for(refresh_token):
        signed_in_as.append(refresh_token)
        return _client()

    monkeypatch.setattr('application.services.check_in_service.get_matcherino_client', _client_for)
    service = CheckInService()
    for tenant in (a, b):
        with tenant_scope(tenant.id):
            await service.create_event(staff, f'{tenant.slug} event', venue_id=VENUE,
                                       status=CheckInEventStatus.OPEN)

    await check_in_sync_worker._tick()

    assert sorted(signed_in_as) == sorted(f'login-{t.slug}' for t in (a, b))
    for tenant in (a, b):
        rows = await CheckInEntrant.filter(tenant_id=tenant.id).prefetch_related('event')
        assert len(rows) == 1
        assert rows[0].event.tenant_id == tenant.id
        badges = await CheckInPass.filter(tenant_id=tenant.id).prefetch_related('tier', 'event')
        assert len(badges) == 1
        assert badges[0].tier.tenant_id == badges[0].event.tenant_id == tenant.id


async def test_each_community_resolves_only_its_own_matcherino_login(two_tenants):
    from application.services.matcherino_login_service import MatcherinoLoginService

    a, b = two_tenants
    await MatcherinoLogin.create(tenant=a, refresh_token='tenant-a-login', matcherino_user_id='1')
    staff = await make_user(discord_id=820, username='s2')
    for tenant in (a, b):
        await UserRole.create(user=staff, role=Role.STAFF, tenant=tenant)
    service = MatcherinoLoginService()

    with tenant_scope(a.id):
        assert await service.resolve() == 'tenant-a-login'
        assert (await service.status(staff)).configured
    with tenant_scope(b.id):
        assert await service.resolve() is None
        assert not (await service.status(staff)).configured
        await service.clear_login(staff)
    assert await MatcherinoLogin.filter(tenant_id=a.id).count() == 1
