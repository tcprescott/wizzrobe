"""Per-event roles: an on-site role is held in one event, Staff covers them all.

A proctor at SGL 2026 isn't one at SGL 2027 until someone says so; a row is
judged by its own event, a community page by any live event, and an archived
event's grants authorise nothing new.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from application.errors import EventArchivedError, NotFoundError
from application.event_context import event_scope
from application.event_writes import archive_maintenance
from application.services import AuthService, EventService, RoleScope, UserService
from application.services.discord import discord_role_mapping_service as drms
from application.services.discord.discord_role_mapping_service import DiscordRoleMappingService
from application.services.volunteer.volunteer_pool_service import VolunteerPoolService
from application.tenant_context import tenant_scope
from models import (
    DiscordRoleMapping,
    Event,
    EventStatus,
    Match,
    Role,
    RoleSource,
    Stage,
    Tenant,
    Tournament,
    User,
    UserRole,
)
from tests.conftest import DEFAULT_TEST_EVENT_ID
from tests.factories import make_member
from theme.dialog.role_grants import also_holds, grant_changes

NOW = datetime(2026, 10, 23, 18, tzinfo=timezone.utc)


@pytest.fixture
async def sgl27(db) -> Event:
    return await Event.create(tenant_id=1, name='SGL 2027', slug='sgl27', status=EventStatus.LIVE)


@pytest.fixture
async def staff(db) -> User:
    user = await make_member(discord_id=9100, username='staff')
    await UserRole.create(user=user, role=Role.STAFF)
    return user


async def _grant(user: User, role: Role, event_id: int | None) -> UserRole:
    return await UserRole.create(user=user, role=role, tenant_id=1, event_id=event_id)


def test_the_on_site_roles_are_per_event_and_the_admin_roles_are_not():
    assert set(Role.per_event()) == {
        Role.PROCTOR, Role.STREAM_MANAGER, Role.TRIFORCE_SUBMITTER, Role.VOLUNTEER_COORDINATOR,
        Role.EQUIPMENT_MANAGER, Role.VOLUNTEER, Role.CHECK_IN_DESK,
    }
    assert Role.community_wide() == [Role.STAFF, Role.PRESET_MANAGER, Role.SYNC_ADMIN, Role.QUALIFIER_ADMIN]
    assert not Role.SUPER_ADMIN.is_per_event


async def test_a_grant_counts_in_its_own_event_only(sgl27):
    person = await make_member(discord_id=9101)
    await _grant(person, Role.PROCTOR, sgl27.id)

    with event_scope(sgl27.id):
        assert await AuthService.has_role(person, Role.PROCTOR)
        assert Role.PROCTOR in await AuthService.get_roles(person)
    with event_scope(DEFAULT_TEST_EVENT_ID):
        assert not await AuthService.has_role(person, Role.PROCTOR)
        assert await AuthService.get_roles(person) == set()


async def test_a_community_role_counts_in_every_event(sgl27, staff):
    for event_id in (DEFAULT_TEST_EVENT_ID, sgl27.id):
        with event_scope(event_id):
            assert await AuthService.has_role(staff, Role.STAFF)


async def test_a_row_is_judged_by_its_own_event(sgl27):
    """A proctor for SGL 2027 can't run an SGL 2026 match, even from SGL 2027's page."""
    person = await make_member(discord_id=9102)
    await _grant(person, Role.PROCTOR, sgl27.id)
    cup = await Tournament.create(name='Cup 2026')
    this_year = await Match.create(tournament=cup, scheduled_at=NOW)
    with event_scope(sgl27.id):
        cup27 = await Tournament.create(name='Cup 2027')
        next_year = await Match.create(tournament=cup27, scheduled_at=NOW)

        assert not await AuthService.can_run_match(person, this_year)
        assert await AuthService.can_run_match(person, next_year)


async def test_a_broadcast_tech_runs_only_their_events_stage_matches(sgl27):
    tech = await make_member(discord_id=9103)
    await _grant(tech, Role.STREAM_MANAGER, DEFAULT_TEST_EVENT_ID)
    stage = await Stage.create(name='Main')
    cup = await Tournament.create(name='Cup')
    on_stage = await Match.create(tournament=cup, scheduled_at=NOW, stage=stage)
    with event_scope(sgl27.id):
        stage27 = await Stage.create(name='Main')
        cup27 = await Tournament.create(name='Cup')
        other = await Match.create(tournament=cup27, scheduled_at=NOW, stage=stage27)
        assert await AuthService.can_run_match(tech, on_stage)
        assert not await AuthService.can_run_match(tech, other)
        assert not await AuthService.can_assign_match_stream(tech, other)


async def test_equipment_counts_the_role_in_any_live_event(sgl27):
    """Equipment is the community's, so a manager from any live event reaches it."""
    manager = await make_member(discord_id=9104)
    await _grant(manager, Role.EQUIPMENT_MANAGER, sgl27.id)
    with event_scope(DEFAULT_TEST_EVENT_ID):
        assert await AuthService.can_manage_equipment(manager)
        assert Role.EQUIPMENT_MANAGER in await AuthService.get_roles(manager, RoleScope.LIVE)

    sgl27.status = EventStatus.ARCHIVED
    await sgl27.save()
    with event_scope(DEFAULT_TEST_EVENT_ID):
        assert not await AuthService.can_manage_equipment(manager)


async def test_granting_lands_in_the_event_on_screen_or_the_one_named(sgl27, staff):
    person = await make_member(discord_id=9105)
    service = UserService()

    with event_scope(sgl27.id):
        await service.grant_role(person, Role.VOLUNTEER, staff)
    await service.grant_role(person, Role.PROCTOR, staff, event_id=sgl27.id)
    await service.grant_role(person, Role.STAFF, staff, event_id=sgl27.id)

    grants = {(r.role, r.event_id) for r in await UserRole.filter(user=person)}
    assert grants == {(Role.VOLUNTEER, sgl27.id), (Role.PROCTOR, sgl27.id), (Role.STAFF, None)}

    await service.revoke_role(person, Role.PROCTOR, staff)
    assert await UserRole.filter(user=person, role=Role.PROCTOR).exists(), 'the default event held none'
    await service.revoke_role(person, Role.PROCTOR, staff, event_id=sgl27.id)
    assert not await UserRole.filter(user=person, role=Role.PROCTOR).exists()


async def test_another_communitys_event_is_refused(staff):
    other = await Tenant.create(name='Elsewhere', slug='elsewhere')
    their_event = await Event.get(tenant_id=other.id, is_default=True)
    person = await make_member(discord_id=9106)

    with pytest.raises(NotFoundError):
        await UserService().grant_role(person, Role.PROCTOR, staff, event_id=their_event.id)
    assert not await UserRole.filter(user=person).exists()


async def test_an_archived_events_grants_cannot_change(sgl27, staff):
    person = await make_member(discord_id=9107)
    await _grant(person, Role.PROCTOR, sgl27.id)
    sgl27.status = EventStatus.ARCHIVED
    await sgl27.save()

    with pytest.raises(EventArchivedError):
        await UserService().grant_role(person, Role.VOLUNTEER, staff, event_id=sgl27.id)
    with pytest.raises(EventArchivedError):
        await UserService().revoke_role(person, Role.PROCTOR, staff, event_id=sgl27.id)


async def test_archived_grants_dont_hold_up_leaving_the_community(sgl27, staff):
    from application.services import TenantMembershipService

    person = await make_member(discord_id=9108)
    with archive_maintenance():
        sgl27.status = EventStatus.ARCHIVED
        await sgl27.save()
        await _grant(person, Role.PROCTOR, sgl27.id)

    assert not await AuthService.holds_any_role(person)
    await TenantMembershipService().remove_member(staff, person)


async def test_a_draft_opens_for_its_own_role_holders(staff):
    draft = await Event.create(tenant_id=1, name='SGL 2028', slug='sgl28', status=EventStatus.DRAFT)
    ref = await EventService.ref(1, draft.id)
    crew = await make_member(discord_id=9109)
    player = await make_member(discord_id=9110)
    await _grant(crew, Role.VOLUNTEER_COORDINATOR, draft.id)

    assert await EventService.can_view(crew, ref)
    assert not await EventService.can_view(player, ref)
    assert draft.id in {e.id for e in await EventService.visible_events(crew)}
    assert draft.id not in {e.id for e in await EventService.visible_events(player)}


async def test_the_volunteer_pool_is_this_events_volunteers(sgl27, staff):
    last_year = await make_member(discord_id=9111)
    this_year = await make_member(discord_id=9112)
    await _grant(last_year, Role.VOLUNTEER, DEFAULT_TEST_EVENT_ID)
    await _grant(this_year, Role.VOLUNTEER, sgl27.id)

    with event_scope(sgl27.id):
        assert await VolunteerPoolService().assignable_user_ids() == {staff.id, this_year.id}


class TestDiscordMappings:
    @pytest.fixture
    async def guild(self, db):
        tenant = await Tenant.get(id=1)
        tenant.discord_guild_id = 42
        await tenant.save()
        return 42

    def _member_of(self, monkeypatch, role_ids: set[int]) -> None:
        fake = type('D', (), {'get_member_role_ids': AsyncMock(return_value=(True, set(role_ids)))})()
        monkeypatch.setattr(drms, 'DiscordService', lambda: fake)

    async def test_a_new_mapping_names_the_default_event(self, guild, sgl27, staff):
        service = DiscordRoleMappingService()
        proctor = await service.add_mapping(guild, 111, 'Proctors', staff, app_role=Role.PROCTOR)
        mods = await service.add_mapping(guild, 112, 'Mods', staff, app_role=Role.STAFF)
        named = await service.add_mapping(guild, 113, 'Crew 27', staff, app_role=Role.VOLUNTEER, event_id=sgl27.id)

        assert (proctor.event_id, mods.event_id, named.event_id) == (DEFAULT_TEST_EVENT_ID, None, sgl27.id)

    async def test_sync_grants_in_the_mappings_event_and_moving_the_default_doesnt_follow(
        self, guild, sgl27, staff, monkeypatch,
    ):
        await DiscordRoleMappingService().add_mapping(guild, 111, 'Proctors', staff, app_role=Role.PROCTOR)
        person = await make_member(discord_id=9113)
        self._member_of(monkeypatch, {111})

        await DiscordRoleMappingService().sync_user_roles(person)
        await EventService.make_default(staff, sgl27.id)
        await DiscordRoleMappingService().sync_user_roles(person)

        rows = await UserRole.filter(user=person)
        assert [(r.role, r.event_id, r.source) for r in rows] == [
            (Role.PROCTOR, DEFAULT_TEST_EVENT_ID, RoleSource.DISCORD),
        ]

    async def test_losing_the_discord_role_takes_back_only_that_events_grant(self, guild, sgl27, staff, monkeypatch):
        await DiscordRoleMappingService().add_mapping(
            guild, 111, 'Proctors', staff, app_role=Role.PROCTOR, event_id=sgl27.id,
        )
        person = await make_member(discord_id=9114)
        await UserService().grant_role(person, Role.PROCTOR, staff)  # by hand, default event
        self._member_of(monkeypatch, {111})
        await DiscordRoleMappingService().sync_user_roles(person)
        self._member_of(monkeypatch, set())
        await DiscordRoleMappingService().sync_user_roles(person)

        rows = await UserRole.filter(user=person)
        assert [(r.role, r.event_id, r.source) for r in rows] == [
            (Role.PROCTOR, DEFAULT_TEST_EVENT_ID, RoleSource.MANUAL),
        ]

    async def test_a_mapping_into_an_archived_event_grants_nothing(self, guild, sgl27, staff, monkeypatch):
        await DiscordRoleMappingService().add_mapping(
            guild, 111, 'Proctors', staff, app_role=Role.PROCTOR, event_id=sgl27.id,
        )
        sgl27.status = EventStatus.ARCHIVED
        await sgl27.save()
        person = await make_member(discord_id=9115)
        self._member_of(monkeypatch, {111})

        summary = await DiscordRoleMappingService().sync_user_roles(person)

        assert summary['granted'] == [] and summary['skipped'] is None
        with pytest.raises(ValueError, match='archived'):
            await DiscordRoleMappingService().add_mapping(
                guild, 112, 'Old crew', staff, app_role=Role.VOLUNTEER, event_id=sgl27.id,
            )

    async def test_mappings_stay_inside_their_community(self, guild, staff):
        other = await Tenant.create(name='Elsewhere', slug='elsewhere')
        their_event = await Event.get(tenant_id=other.id, is_default=True)
        with pytest.raises(NotFoundError):
            await DiscordRoleMappingService().add_mapping(
                guild, 111, 'Proctors', staff, app_role=Role.PROCTOR, event_id=their_event.id,
            )
        assert not await DiscordRoleMapping.filter(tenant_id=1).exists()
        with tenant_scope(other.id):
            assert not await DiscordRoleMapping.filter(tenant_id=other.id).exists()


class TestEditMemberRoles:
    """The pure halves of the dialog's roles section (layout A)."""

    def _events(self):
        from application.services.event_service import EventRef

        def ref(i, name, status, default=False):
            return EventRef(id=i, tenant_id=1, name=name, slug=name.lower().replace(' ', ''),
                            status=status, is_default=default, starts_on=None, ends_on=None)
        return [ref(1, 'SGL 2026', EventStatus.LIVE, True), ref(2, 'SGL 2027', EventStatus.DRAFT),
                ref(3, 'SGL 2025', EventStatus.ARCHIVED)]

    def test_also_holds_names_the_other_events(self):
        held = [(Role.STAFF, None), (Role.PROCTOR, 1), (Role.VOLUNTEER_COORDINATOR, 2), (Role.PROCTOR, 3)]
        assert also_holds(held, self._events(), 1) == (
            'Also holds: Volunteer Coordinator in SGL 2027 (Draft); Proctor in SGL 2025 (Archived).'
        )
        assert also_holds([(Role.PROCTOR, 1)], self._events(), 1) == ''

    def test_only_the_events_opened_in_the_picker_change(self):
        held = [(Role.STAFF, None), (Role.PROCTOR, 1), (Role.VOLUNTEER, 2)]
        granting, revoking = grant_changes(held, [Role.STAFF, Role.SYNC_ADMIN], {1: {Role.VOLUNTEER}})
        assert granting == [(Role.SYNC_ADMIN, None), (Role.VOLUNTEER, 1)]
        assert revoking == [(Role.PROCTOR, 1)]


async def test_community_roles_count_under_every_scope(staff):
    for scope in (RoleScope.LIVE, RoleScope.OPEN, RoleScope.EVERY, None, DEFAULT_TEST_EVENT_ID):
        assert await AuthService.get_roles(staff, scope) == {Role.STAFF}
    assert await AuthService.holds_any_role(staff)
