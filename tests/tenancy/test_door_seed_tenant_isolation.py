"""DoorSeed: one community's seed page never opens under another community."""

import pytest

from application.errors import NotFoundError
from application.repositories import DoorSeedRepository
from application.services.door_seed_service import DoorSeedService
from application.tenant_context import tenant_scope
from models import Role, User, UserRole


async def test_a_seed_is_only_found_in_its_own_community(two_tenants):
    a, b = two_tenants
    with tenant_scope(a.id):
        await DoorSeedRepository.create(token='tok-a', version='1.5.7-u', spoiler_access='staff')
    with tenant_scope(b.id):
        assert await DoorSeedRepository.get_by_token('tok-a') is None
        with pytest.raises(NotFoundError):
            await DoorSeedService().get_seed('tok-a')
    with tenant_scope(a.id):
        assert (await DoorSeedService().get_seed('tok-a')).token == 'tok-a'


async def test_another_communitys_staff_cannot_read_a_staff_spoiler(two_tenants):
    a, b = two_tenants
    outsider = await User.create(discord_id=710, username='other-staff')
    await UserRole.create(user=outsider, role=Role.STAFF, tenant=b)
    with tenant_scope(a.id):
        seed = await DoorSeedRepository.create(token='tok-s', version='1.5.7-u', spoiler_access='staff')
        assert not await DoorSeedService.can_read_spoiler(seed, outsider)
