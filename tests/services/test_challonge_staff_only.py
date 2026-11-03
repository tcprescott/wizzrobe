"""Linking and unlinking a Challonge bracket is staff's job; syncing is not.

Thomas's rule (2026-10-18), the same for Challonge and start.gg: a tournament's
admins sync its bracket, only staff link and unlink one.
"""

import pytest

from application.repositories.user_role_repository import UserRoleRepository
from application.services.challonge_service import ChallongeService
from models import Role, TenantMembership, Tournament, User


@pytest.fixture
async def linked(db):
    return await Tournament.create(name='Bracket Cup', challonge_tournament_id='T1')


@pytest.fixture
async def tournament_admin(linked, db):
    ta = await User.create(discord_id=6100, username='ta')
    await TenantMembership.create(user=ta, tenant_id=1)
    await linked.admins.add(ta)
    return ta


async def test_a_tournament_admin_cannot_link_or_unlink(linked, tournament_admin):
    service = ChallongeService()
    with pytest.raises(PermissionError):
        await service.link_tournament(linked.id, 'T2', tournament_admin)
    with pytest.raises(PermissionError):
        await service.unlink_tournament(linked.id, tournament_admin)


async def test_a_tournament_admin_can_sync(linked, tournament_admin, monkeypatch):
    async def fake_sync(self, tournament, actor, force=False):
        return {'participants': 0, 'matches': 0}

    monkeypatch.setattr(ChallongeService, '_sync_tournament', fake_sync)
    assert await ChallongeService().sync_bracket(linked.id, tournament_admin, force=True) == {
        'participants': 0, 'matches': 0,
    }


async def test_a_plain_member_cannot_sync(linked, db):
    member = await User.create(discord_id=6102, username='member')
    await TenantMembership.create(user=member, tenant_id=1)
    with pytest.raises(PermissionError):
        await ChallongeService().sync_bracket(linked.id, member, force=True)


async def test_staff_pass_the_gate(linked, db):
    boss = await User.create(discord_id=6101, username='boss')
    await UserRoleRepository.add(boss, Role.STAFF)
    await TenantMembership.create(user=boss, tenant_id=1)
    unlinked = await ChallongeService().unlink_tournament(linked.id, boss)
    assert unlinked.challonge_tournament_id is None
