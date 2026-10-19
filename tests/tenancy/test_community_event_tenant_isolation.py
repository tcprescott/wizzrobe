"""Community events and their subscriptions never cross communities."""

from datetime import datetime, timedelta, timezone

import pytest

from application.services.community_event_service import CommunityEventService, EventDraft
from application.tenant_context import tenant_scope
from models import CommunityEvent, CommunityEventSeen, CommunityEventSubscription, Role, TenantMembership, UserRole
from tests.factories import make_user


def _draft(title: str) -> EventDraft:
    return EventDraft(title=title, description='', duration_minutes=60,
                      starts_at=datetime.now(timezone.utc) + timedelta(hours=3))


async def test_community_events_do_not_leak_across_tenants(two_tenants):
    a, b = two_tenants
    member = await make_user(discord_id=720, username='both-member')
    staff = await make_user(discord_id=721, username='both-staff')
    for tenant in (a, b):
        await TenantMembership.create(user=member, tenant=tenant)
        await TenantMembership.create(user=staff, tenant=tenant)
        await UserRole.create(user=staff, role=Role.STAFF, tenant=tenant)
    service = CommunityEventService()

    with tenant_scope(a.id):
        listed = await service.submit(staff, _draft('A listed'))
        pending = await service.submit(member, _draft('A pending'))
        await service.subscribe(member, listed.id)

    with tenant_scope(b.id):
        assert await service.list_upcoming(member) == []
        assert await service.list_mine(member) == []
        assert await service.list_for_review(staff) == []
        assert await service.pending_count(staff) == 0
        for call in (
            service.get_event(member, listed.id),
            service.subscribe(member, listed.id),
            service.approve(staff, pending.id),
            service.reject(staff, listed.id, 'no'),
            service.cancel(staff, listed.id),
        ):
            with pytest.raises(ValueError):
                await call
        assert await service.unseen_ids(member) == set()
        window = (datetime.now(timezone.utc), datetime.now(timezone.utc) + timedelta(days=1))
        assert await service.list_attending(member, *window) == []
        assert await service.list_attending(staff, *window) == []
        await service.mark_seen(member)
        mine, counts = await service.subscription_state(member, [listed.id])
        assert mine == set() and counts == {}
        await service.unsubscribe(member, listed.id)

    assert await CommunityEventSubscription.filter(event_id=listed.id).count() == 1
    assert (await CommunityEvent.get(id=pending.id)).status.value == 'pending'
    assert (await CommunityEvent.get(id=listed.id)).status.value == 'approved'

    with tenant_scope(a.id):
        # Opening B's listing marked nothing seen in A.
        assert await service.unseen_ids(member) == {listed.id}
        assert await CommunityEventSeen.filter(user=member).count() == 1
