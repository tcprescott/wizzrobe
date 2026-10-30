"""The derived setup checklist.

Nothing about a community's setup progress is stored, so every one of these
asserts a *derivation* — including that it runs backwards when rows are deleted,
which a stored ``setup_complete`` column could not do.
"""

import pytest

from application.repositories.user_role_repository import UserRoleRepository
from application.services.system_config_service import (
    KEY_EVENT_END_DATE,
    KEY_EVENT_START_DATE,
    SystemConfigService,
)
from application.services.tenant_setup_service import TenantSetupService
from application.tenant_context import tenant_scope
from models import Role, Stage, Tenant, Tournament, TournamentPlayers, User


def _by_key(steps):
    return {s.key: s for s in steps}


async def test_a_fresh_tenant_has_no_completed_steps(db):
    fresh = await Tenant.create(name='Fresh', slug='fresh')
    with tenant_scope(fresh.id):
        steps = await TenantSetupService().status()
    assert not any(s.done for s in steps)
    assert TenantSetupService.is_ready(steps) is False


async def test_granting_staff_completes_the_staff_step(db):
    fresh = await Tenant.create(name='Fresh', slug='fresh')
    user = await User.create(discord_id=9001, username='first_admin')
    with tenant_scope(fresh.id):
        assert _by_key(await TenantSetupService().status())['staff'].done is False
        await UserRoleRepository.add(user, Role.STAFF)
        assert _by_key(await TenantSetupService().status())['staff'].done is True


async def test_enrolment_step_needs_a_player_not_just_a_tournament(db):
    fresh = await Tenant.create(name='Fresh', slug='fresh')
    user = await User.create(discord_id=9002, username='player')
    with tenant_scope(fresh.id):
        tournament = await Tournament.create(name='T')
        steps = _by_key(await TenantSetupService().status())
        assert steps['tournament'].done is True
        assert steps['enrolment'].done is False

        await TournamentPlayers.create(tournament=tournament, user=user)
        assert _by_key(await TenantSetupService().status())['enrolment'].done is True


async def test_is_ready_ignores_the_advisory_steps(db):
    fresh = await Tenant.create(name='Fresh', slug='fresh')
    user = await User.create(discord_id=9003, username='u')
    with tenant_scope(fresh.id):
        await UserRoleRepository.add(user, Role.STAFF)
        tournament = await Tournament.create(name='T')
        await TournamentPlayers.create(tournament=tournament, user=user)
        steps = await TenantSetupService().status()

    assert _by_key(steps)['stage'].done is False
    assert _by_key(steps)['event_window'].done is False
    assert TenantSetupService.is_ready(steps) is True
    assert TenantSetupService.outstanding(steps) == []


async def test_advisory_steps_still_complete_when_satisfied(db):
    fresh = await Tenant.create(name='Fresh', slug='fresh')
    staffer = await User.create(discord_id=9004, username='cfg')
    with tenant_scope(fresh.id):
        await UserRoleRepository.add(staffer, Role.STAFF)
        await Stage.create(name='Stage 1')
        await SystemConfigService.set_raw(KEY_EVENT_START_DATE, '2026-01-01', staffer)
        await SystemConfigService.set_raw(KEY_EVENT_END_DATE, '2026-01-03', staffer)
        steps = _by_key(await TenantSetupService().status())
    assert steps['stage'].done is True
    assert steps['event_window'].done is True


async def test_deleting_the_last_tournament_makes_the_tenant_unready_again(db):
    fresh = await Tenant.create(name='Fresh', slug='fresh')
    user = await User.create(discord_id=9005, username='u2')
    with tenant_scope(fresh.id):
        await UserRoleRepository.add(user, Role.STAFF)
        tournament = await Tournament.create(name='T')
        await TournamentPlayers.create(tournament=tournament, user=user)
        assert TenantSetupService.is_ready(await TenantSetupService().status()) is True

        await tournament.delete()
        steps = await TenantSetupService().status()

    assert TenantSetupService.is_ready(steps) is False
    assert {s.key for s in TenantSetupService.outstanding(steps)} == {'tournament', 'enrolment'}


async def test_status_for_reads_the_named_tenant_not_the_ambient_one(db):
    a = await Tenant.get(id=1)
    b = await Tenant.create(name='B', slug='b')
    user = await User.create(discord_id=9006, username='u3')
    with tenant_scope(a.id):
        await UserRoleRepository.add(user, Role.STAFF)
        await Tournament.create(name='A tournament')

    steps_b = _by_key(await TenantSetupService.status_for(b.id))
    assert steps_b['staff'].done is False
    assert steps_b['tournament'].done is False

    steps_a = _by_key(await TenantSetupService.status_for(a.id))
    assert steps_a['staff'].done is True
    assert steps_a['tournament'].done is True


async def test_the_fledgling_seed_tenant_is_not_setup_complete(seeded_db):
    fledgling = await Tenant.get(slug='fledgling')
    steps = _by_key(await TenantSetupService.status_for(fledgling.id))

    assert steps['staff'].done is True
    assert steps['tournament'].done is True
    assert steps['enrolment'].done is False
    assert steps['stage'].done is False
    assert TenantSetupService.is_ready(list(steps.values())) is False

    default = await Tenant.get(slug='default')
    assert TenantSetupService.is_ready(await TenantSetupService.status_for(default.id)) is True


@pytest.mark.parametrize('required_key', ['staff', 'tournament', 'enrolment'])
async def test_each_required_step_alone_blocks_readiness(db, required_key):
    fresh = await Tenant.create(name='Fresh', slug='fresh')
    user = await User.create(discord_id=9007, username='u4')
    with tenant_scope(fresh.id):
        if required_key != 'staff':
            await UserRoleRepository.add(user, Role.STAFF)
        if required_key != 'tournament':
            tournament = await Tournament.create(name='T')
            if required_key != 'enrolment':
                await TournamentPlayers.create(tournament=tournament, user=user)
        steps = await TenantSetupService().status()
    assert TenantSetupService.is_ready(steps) is False
