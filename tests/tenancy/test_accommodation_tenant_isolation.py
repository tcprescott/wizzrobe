"""ADA accommodation requests never cross communities.

The point of storing them per tenant: a member who asks one community for an
accommodation tells that community's staff and nobody else's, even when the
same person is a member (or a staff member) of both.
"""

from application.services.accommodation_service import AccommodationService
from application.tenant_context import tenant_scope
from models import AccommodationRequest, Role, TenantMembership, UserRole
from tests.factories import make_user


async def test_requests_do_not_leak_across_tenants(two_tenants):
    a, b = two_tenants
    member = await make_user(discord_id=700, username='both-member')
    staff = await make_user(discord_id=701, username='both-staff')
    for tenant in (a, b):
        await TenantMembership.create(user=member, tenant=tenant)
        await UserRole.create(user=staff, role=Role.STAFF, tenant=tenant)

    service = AccommodationService()
    with tenant_scope(a.id):
        mine_a = await service.set_my_request(member, True, 'asked A only')

    with tenant_scope(b.id):
        assert await service.get_mine(member) is None
        assert await service.list_requests(staff, include_withdrawn=True) == []
        assert await service.requesting_user_ids(staff) == set()
        try:
            await service.update_request(staff, mine_a.id, 'arranged', 'x')
        except ValueError:
            pass
        else:
            raise AssertionError('cross-tenant update was allowed')
        mine_b = await service.set_my_request(member, True, 'asked B')

    assert mine_b.id != mine_a.id
    await mine_a.refresh_from_db()
    assert mine_a.details == 'asked A only'
    assert mine_a.status.value == 'new'
    with tenant_scope(a.id):
        assert [r.id for r in await service.list_requests(staff)] == [mine_a.id]
    assert await AccommodationRequest.filter(user=member).count() == 2


async def test_schedule_notes_do_not_leak_across_tenants(two_tenants):
    a, b = two_tenants
    member = await make_user(discord_id=710, username='m2')
    staff = await make_user(discord_id=711, username='s2')
    for tenant in (a, b):
        await TenantMembership.create(user=member, tenant=tenant)
        await UserRole.create(user=staff, role=Role.STAFF, tenant=tenant)
    service = AccommodationService()
    with tenant_scope(a.id):
        request = await service.set_my_request(member, True, 'x')
        await service.update_request(staff, request.id, 'arranged', 'A-only note')
        assert await service.arranged_notes_for(staff, [member.id]) == {member.id: 'A-only note'}
    with tenant_scope(b.id):
        assert await service.arranged_notes_for(staff, [member.id]) == {}


async def test_the_action_count_and_users_column_do_not_leak_across_tenants(two_tenants):
    a, b = two_tenants
    member = await make_user(discord_id=720, username='m3')
    staff = await make_user(discord_id=721, username='s3')
    for tenant in (a, b):
        await TenantMembership.create(user=member, tenant=tenant)
        await UserRole.create(user=staff, role=Role.STAFF, tenant=tenant)
    service = AccommodationService()
    with tenant_scope(a.id):
        request = await service.set_my_request(member, True, 'x')
        await service.update_request(staff, request.id, 'arranged', None)
        await service.set_my_request(member, True, 'changed after arranging')
        assert await service.action_needed_count(staff) == 1
        assert set(await service.open_requests_by_user(staff)) == {member.id}
    with tenant_scope(b.id):
        assert await service.action_needed_count(staff) == 0
        assert await service.open_requests_by_user(staff) == {}
        assert await service.get_request(staff, request.id) is None
