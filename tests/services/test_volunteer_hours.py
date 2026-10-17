"""Volunteer hours — the interval fold and the comp-tier totals.

The fold is exercised as a pure function (no database, readable failures); the
service is exercised against a real SQLite tenant because the rules that matter
— drafts excluded, published counted, the event window — live in the query.
"""

from datetime import datetime, timezone

import pytest

from application.errors import FeatureDisabledError
from application.services.feature_flag_service import reset_flag_cache
from application.services.user_service import UserService
from application.services.volunteer.volunteer_hours import VolunteerHoursService, merged_hours
from application.tenant_context import tenant_scope
from models import (
    FeatureFlag,
    Role,
    SystemConfiguration,
    Tenant,
    TenantFeatureFlag,
    User,
    UserRole,
    VolunteerAssignment,
    VolunteerPosition,
    VolunteerShift,
)

UTC = timezone.utc


def _dt(hour: int, day: int = 4, minute: int = 0) -> datetime:
    return datetime(2026, 10, day, hour, minute, tzinfo=UTC)


# ---------------------------------------------------------------------------
# merged_hours — the pure fold
# ---------------------------------------------------------------------------


class TestMergedHours:
    def test_empty(self):
        assert merged_hours([]) == 0.0

    def test_single_window(self):
        assert merged_hours([(_dt(8), _dt(12))]) == 4.0

    def test_disjoint_windows_add(self):
        assert merged_hours([(_dt(8), _dt(12)), (_dt(16), _dt(20))]) == 8.0

    def test_partial_overlap_counts_once(self):
        assert merged_hours([(_dt(8), _dt(12)), (_dt(10), _dt(14))]) == 6.0

    def test_overlap_order_does_not_matter(self):
        assert merged_hours([(_dt(10), _dt(14)), (_dt(8), _dt(12))]) == 6.0

    def test_nested_window_adds_nothing(self):
        assert merged_hours([(_dt(8), _dt(16)), (_dt(10), _dt(12))]) == 8.0

    def test_touching_windows_merge_without_double_count(self):
        assert merged_hours([(_dt(8), _dt(12)), (_dt(12), _dt(16))]) == 8.0

    def test_touching_windows_lose_no_minute(self):
        windows = [(_dt(8), _dt(12)), (_dt(12), _dt(12, minute=30))]
        assert merged_hours(windows) == 4.5

    def test_zero_length_window_dropped(self):
        assert merged_hours([(_dt(8), _dt(8))]) == 0.0

    def test_inverted_window_does_not_subtract(self):
        assert merged_hours([(_dt(8), _dt(12)), (_dt(16), _dt(14))]) == 4.0

    def test_three_way_chain_merges_into_one_block(self):
        windows = [(_dt(8), _dt(12)), (_dt(11), _dt(15)), (_dt(14), _dt(18))]
        assert merged_hours(windows) == 10.0


# ---------------------------------------------------------------------------
# VolunteerHoursService — against a real tenant
# ---------------------------------------------------------------------------


@pytest.fixture
async def volunteer_tenant(db):
    """A tenant with the VOLUNTEERS flag live and a three-day event window."""
    tenant = await Tenant.create(name='Hours', slug='hours')
    await TenantFeatureFlag.create(
        tenant_id=tenant.id, flag=FeatureFlag.VOLUNTEERS.value,
        available=True, enabled=True,
    )
    reset_flag_cache()
    for key, value in (
        ('event_start_date', '2026-10-04'),
        ('event_end_date', '2026-10-06'),
        ('volunteer_comp_tiers', '8, 12, 16'),
    ):
        await SystemConfiguration.create(name=key, value=value, tenant_id=tenant.id)
    return tenant


@pytest.fixture
async def fixtures(volunteer_tenant):
    with tenant_scope(volunteer_tenant.id):
        position = await VolunteerPosition.create(
            name='Check-in Desk', tenant_id=volunteer_tenant.id,
        )
        user = await User.create(discord_id=91001, username='vol')
        await UserRole.create(user=user, role=Role.VOLUNTEER, tenant_id=volunteer_tenant.id)
        yield {'tenant': volunteer_tenant, 'position': position, 'user': user}


async def _assign(fixtures, start, end, *, auto_generated=False, user=None):
    shift = await VolunteerShift.create(
        position=fixtures['position'], starts_at=start, ends_at=end,
        tenant_id=fixtures['tenant'].id,
    )
    return await VolunteerAssignment.create(
        shift=shift, user=user or fixtures['user'],
        tenant_id=fixtures['tenant'].id, auto_generated=auto_generated,
    )


class TestForUser:
    async def test_draft_contributes_nothing(self, fixtures):
        await _assign(fixtures, _dt(8), _dt(12), auto_generated=True)
        with tenant_scope(fixtures['tenant'].id):
            summary = await VolunteerHoursService().for_user(fixtures['user'])
        assert summary.hours == 0.0
        assert summary.tier_cleared is None

    async def test_publishing_the_draft_makes_it_count(self, fixtures):
        assignment = await _assign(fixtures, _dt(8), _dt(12), auto_generated=True)
        assignment.auto_generated = False
        await assignment.save()
        with tenant_scope(fixtures['tenant'].id):
            summary = await VolunteerHoursService().for_user(fixtures['user'])
        assert summary.hours == 4.0

    async def test_unacknowledged_assignment_counts_in_full(self, fixtures):
        assignment = await _assign(fixtures, _dt(8), _dt(12))
        assert assignment.acknowledged_at is None
        with tenant_scope(fixtures['tenant'].id):
            summary = await VolunteerHoursService().for_user(fixtures['user'])
        assert summary.hours == 4.0

    async def test_overlapping_shifts_count_the_overlap_once(self, fixtures):
        await _assign(fixtures, _dt(8), _dt(12))
        await _assign(fixtures, _dt(10), _dt(14))
        with tenant_scope(fixtures['tenant'].id):
            summary = await VolunteerHoursService().for_user(fixtures['user'])
        assert summary.hours == 6.0

    async def test_nested_shift_adds_nothing(self, fixtures):
        await _assign(fixtures, _dt(8), _dt(16))
        await _assign(fixtures, _dt(10), _dt(12))
        with tenant_scope(fixtures['tenant'].id):
            summary = await VolunteerHoursService().for_user(fixtures['user'])
        assert summary.hours == 8.0

    async def test_touching_shifts_are_one_block(self, fixtures):
        await _assign(fixtures, _dt(8), _dt(12))
        await _assign(fixtures, _dt(12), _dt(16))
        with tenant_scope(fixtures['tenant'].id):
            summary = await VolunteerHoursService().for_user(fixtures['user'])
        assert summary.hours == 8.0

    async def test_exactly_eight_hours_clears_the_eight_hour_tier(self, fixtures):
        await _assign(fixtures, _dt(8), _dt(12))
        await _assign(fixtures, _dt(12), _dt(16))
        with tenant_scope(fixtures['tenant'].id):
            summary = await VolunteerHoursService().for_user(fixtures['user'])
        assert summary.tier_cleared == 8.0
        assert summary.next_tier == 12.0
        assert summary.hours_to_next == 4.0

    async def test_below_the_first_tier_clears_nothing(self, fixtures):
        await _assign(fixtures, _dt(8), _dt(12))
        with tenant_scope(fixtures['tenant'].id):
            summary = await VolunteerHoursService().for_user(fixtures['user'])
        assert summary.tier_cleared is None
        assert summary.next_tier == 8.0
        assert summary.hours_to_next == 4.0

    async def test_top_tier_leaves_no_next(self, fixtures):
        for day in (4, 5, 6):
            await _assign(fixtures, _dt(8, day=day), _dt(14, day=day))
        with tenant_scope(fixtures['tenant'].id):
            summary = await VolunteerHoursService().for_user(fixtures['user'])
        assert summary.tier_cleared == 16.0
        assert summary.next_tier is None
        assert summary.hours_to_next is None

    async def test_assignments_outside_the_event_window_are_excluded(self, fixtures):
        # The event window ends 2026-10-06; this one is a fortnight later.
        await _assign(fixtures, _dt(8, day=20), _dt(16, day=20))
        with tenant_scope(fixtures['tenant'].id):
            summary = await VolunteerHoursService().for_user(fixtures['user'])
        assert summary.hours == 0.0

    async def test_explicit_window_overrides_the_event_window(self, fixtures):
        await _assign(fixtures, _dt(8, day=20), _dt(16, day=20))
        with tenant_scope(fixtures['tenant'].id):
            summary = await VolunteerHoursService().for_user(
                fixtures['user'],
                start=datetime(2026, 10, 20).date(),
                end=datetime(2026, 10, 20).date(),
            )
        assert summary.hours == 8.0


class TestRoster:
    async def test_lists_role_holders_with_no_hours(self, fixtures):
        with tenant_scope(fixtures['tenant'].id):
            summaries = await VolunteerHoursService().roster(actor=await UserService().get_system_user())
        assert [s.user_id for s in summaries] == [fixtures['user'].id]
        assert summaries[0].hours == 0.0

    async def test_orders_by_hours_descending(self, fixtures):
        other = await User.create(discord_id=91002, username='vol2')
        with tenant_scope(fixtures['tenant'].id):
            await UserRole.create(user=other, role=Role.VOLUNTEER, tenant_id=fixtures['tenant'].id)
        await _assign(fixtures, _dt(8), _dt(12), user=other)
        with tenant_scope(fixtures['tenant'].id):
            summaries = await VolunteerHoursService().roster(actor=await UserService().get_system_user())
        assert [s.user_id for s in summaries] == [other.id, fixtures['user'].id]

    async def test_drafts_do_not_count_on_the_roster(self, fixtures):
        await _assign(fixtures, _dt(8), _dt(12), auto_generated=True)
        with tenant_scope(fixtures['tenant'].id):
            summaries = await VolunteerHoursService().roster(actor=await UserService().get_system_user())
        assert summaries[0].hours == 0.0


    async def test_refuses_a_plain_volunteer(self, fixtures):
        with tenant_scope(fixtures['tenant'].id):
            with pytest.raises(PermissionError):
                await VolunteerHoursService().roster(actor=fixtures['user'])


class TestFeatureGate:
    async def test_for_user_refuses_when_the_feature_is_off(self, db):
        tenant = await Tenant.create(name='Bare', slug='bare-hours')
        user = await User.create(discord_id=91003, username='vol3')
        with tenant_scope(tenant.id):
            with pytest.raises(FeatureDisabledError):
                await VolunteerHoursService().for_user(user)

    async def test_roster_refuses_when_the_feature_is_off(self, db):
        tenant = await Tenant.create(name='Bare', slug='bare-hours2')
        with tenant_scope(tenant.id):
            with pytest.raises(FeatureDisabledError):
                await VolunteerHoursService().roster(actor=None)


# ---------------------------------------------------------------------------
# Tier configuration
# ---------------------------------------------------------------------------


class TestCompTiers:
    async def test_defaults_when_unset(self, db):
        from application.services import SystemConfigService
        assert await SystemConfigService.get_volunteer_comp_tiers() == [8.0, 12.0, 16.0]

    async def test_reads_a_configured_list(self, db):
        from application.services import SystemConfigService
        await SystemConfiguration.create(name='volunteer_comp_tiers', value='10,20,5')
        assert await SystemConfigService.get_volunteer_comp_tiers() == [5.0, 10.0, 20.0]

    async def test_malformed_value_falls_back_to_the_default(self, db):
        from application.services import SystemConfigService
        await SystemConfiguration.create(name='volunteer_comp_tiers', value='eight, twelve')
        assert await SystemConfigService.get_volunteer_comp_tiers() == [8.0, 12.0, 16.0]

    async def test_zero_turns_tiers_off(self, db):
        from application.services import SystemConfigService
        await SystemConfiguration.create(name='volunteer_comp_tiers', value='0')
        assert await SystemConfigService.get_volunteer_comp_tiers() == []

    async def test_no_tiers_leaves_a_summary_without_one(self, fixtures):
        await SystemConfiguration.filter(
            name='volunteer_comp_tiers', tenant_id=fixtures['tenant'].id,
        ).update(value='0')
        await _assign(fixtures, _dt(8), _dt(16))
        with tenant_scope(fixtures['tenant'].id):
            summary = await VolunteerHoursService().for_user(fixtures['user'])
        assert summary.hours == 8.0
        assert summary.tier_cleared is None
        assert summary.next_tier is None
        assert summary.hours_to_next is None
