"""Tests for VolunteerExportService.

The export is what a coordinator takes into a spreadsheet, so the assertions are
about the *content* of each sheet: that qualifications and declared availability
travel with the roster, that open slots survive into the schedule grid, and that
the range and permission gates hold.
"""

import itertools

import pytest

from application.services.volunteer.volunteer_export_service import VolunteerExportService
from application.tenant_context import tenant_scope
from models import (
    Role,
    UserRole,
    VolunteerAssignment,
    VolunteerAvailability,
    VolunteerAvailabilityStatus,
    VolunteerPosition,
    VolunteerQualification,
    VolunteerShift,
)
from tests.factories import make_user, utc

_next_discord_id = itertools.count(900000)


async def _person(name, *, roles=()):
    user = await make_user(next(_next_discord_id), username=name, display_name=name)
    for role in roles:
        await UserRole.create(user=user, role=role)
    return user


async def _coordinator():
    return await _person('coord', roles=[Role.VOLUNTEER_COORDINATOR])


WINDOW_START = utc(2026, 10, 4, 0)
WINDOW_END = utc(2026, 10, 5, 0)


async def _fixture_data():
    """A position, one 4-hour 2-slot shift, one volunteer who can fill it."""
    position = await VolunteerPosition.create(name='Check-in', description='Front desk')
    shift = await VolunteerShift.create(
        position=position, starts_at=utc(2026, 10, 4, 12), ends_at=utc(2026, 10, 4, 16),
        label='Shift 2', slots_needed=2,
    )
    volunteer = await _person('Alex', roles=[Role.VOLUNTEER])
    await VolunteerQualification.create(user=volunteer, position=position)
    await VolunteerAvailability.create(
        user=volunteer, starts_at=utc(2026, 10, 4, 12), ends_at=utc(2026, 10, 4, 20),
        status=VolunteerAvailabilityStatus.PREFERRED, note='Happy to close',
    )
    return position, shift, volunteer


class TestBuild:
    async def test_sheets_cover_every_dataset(self, db):
        actor = await _coordinator()
        await _fixture_data()

        bundle = await VolunteerExportService().build(actor, WINDOW_START, WINDOW_END)

        assert [s.name for s in bundle.sheets] == [
            'volunteers', 'availability', 'positions', 'shifts', 'assignments', 'schedule',
        ]

    async def test_volunteers_sheet_carries_qualifications(self, db):
        actor = await _coordinator()
        await _fixture_data()

        bundle = await VolunteerExportService().build(actor, WINDOW_START, WINDOW_END)
        row = next(r for r in bundle.sheet('volunteers').rows if r['name'] == 'Alex')

        assert row['qualifications'] == 'Check-in'
        assert row['in_volunteer_pool'] is True
        assert row['availability_windows'] == 1
        assert row['available_hours'] == 8.0

    async def test_overlapping_shifts_count_once_in_assigned_hours(self, db):
        actor = await _coordinator()
        position, shift, volunteer = await _fixture_data()
        overlapping = await VolunteerShift.create(
            position=position, starts_at=utc(2026, 10, 4, 14), ends_at=utc(2026, 10, 4, 18),
            slots_needed=1,
        )
        await VolunteerAssignment.create(shift=shift, user=volunteer)
        await VolunteerAssignment.create(shift=overlapping, user=volunteer)

        bundle = await VolunteerExportService().build(actor, WINDOW_START, WINDOW_END)
        row = next(r for r in bundle.sheet('volunteers').rows if r['name'] == 'Alex')

        assert row['assigned_shifts'] == 2
        assert row['assigned_hours'] == 6.0

    async def test_unavailable_windows_export_but_do_not_count_as_available(self, db):
        actor = await _coordinator()
        _, _, volunteer = await _fixture_data()
        await VolunteerAvailability.create(
            user=volunteer, starts_at=utc(2026, 10, 4, 2), ends_at=utc(2026, 10, 4, 6),
            status=VolunteerAvailabilityStatus.UNAVAILABLE,
        )

        bundle = await VolunteerExportService().build(actor, WINDOW_START, WINDOW_END)
        row = next(r for r in bundle.sheet('volunteers').rows if r['name'] == 'Alex')
        statuses = {r['status'] for r in bundle.sheet('availability').rows}

        assert 'Unavailable' in statuses
        assert row['availability_windows'] == 2
        assert row['available_hours'] == 8.0

    async def test_availability_rows_carry_local_and_utc_times(self, db):
        actor = await _coordinator()
        await _fixture_data()

        bundle = await VolunteerExportService().build(actor, WINDOW_START, WINDOW_END)
        row = bundle.sheet('availability').rows[0]

        assert row['date_local'] == '2026-10-04'
        assert row['start_local'] == '08:00'  # 12:00 UTC in EDT
        assert row['starts_at_utc'] == '2026-10-04T12:00:00+00:00'
        assert row['note'] == 'Happy to close'

    async def test_shift_row_reports_open_slots(self, db):
        actor = await _coordinator()
        _, shift, volunteer = await _fixture_data()
        await VolunteerAssignment.create(shift=shift, user=volunteer, assigned_by=actor)

        bundle = await VolunteerExportService().build(actor, WINDOW_START, WINDOW_END)
        row = bundle.sheet('shifts').rows[0]

        assert row['slots_needed'] == 2
        assert row['filled'] == 1
        assert row['open'] == 1
        assert row['assigned'] == 'Alex'
        assert row['hours'] == 4.0
        assert row['day'] == 'Sunday'

    async def test_schedule_grid_includes_open_slots(self, db):
        actor = await _coordinator()
        _, shift, volunteer = await _fixture_data()
        await VolunteerAssignment.create(shift=shift, user=volunteer, assigned_by=actor)

        bundle = await VolunteerExportService().build(actor, WINDOW_START, WINDOW_END)
        rows = bundle.sheet('schedule').rows

        assert len(rows) == 2
        assert rows[0]['volunteer'] == 'Alex'
        assert rows[0]['state'] == 'Assigned'
        assert rows[1]['volunteer'] == ''
        assert rows[1]['state'] == 'Open'

    async def test_schedule_grid_marks_drafts_and_extras(self, db):
        actor = await _coordinator()
        position, _, volunteer = await _fixture_data()
        single = await VolunteerShift.create(
            position=position, starts_at=utc(2026, 10, 4, 18), ends_at=utc(2026, 10, 4, 20),
            slots_needed=1,
        )
        other = await _person('Blair', roles=[Role.VOLUNTEER])
        await VolunteerAssignment.create(shift=single, user=volunteer, auto_generated=True)
        await VolunteerAssignment.create(shift=single, user=other)

        bundle = await VolunteerExportService().build(actor, WINDOW_START, WINDOW_END)
        states = [r['state'] for r in bundle.sheet('schedule').rows if r['shift_id'] == single.id]

        assert states == ['Draft', 'Extra']

    async def test_assignment_row_flags_availability_mismatch(self, db):
        actor = await _coordinator()
        position, _, volunteer = await _fixture_data()
        early = await VolunteerShift.create(
            position=position, starts_at=utc(2026, 10, 4, 4), ends_at=utc(2026, 10, 4, 8),
        )
        await VolunteerAssignment.create(shift=early, user=volunteer, assigned_by=actor)

        bundle = await VolunteerExportService().build(actor, WINDOW_START, WINDOW_END)
        row = next(r for r in bundle.sheet('assignments').rows if r['shift_id'] == early.id)

        assert row['stated_availability'] == 'Not stated'
        assert row['source'] == 'Coordinator'
        assert row['assigned_by'] == 'coord'

    async def test_assignment_row_reports_matching_availability(self, db):
        actor = await _coordinator()
        _, shift, volunteer = await _fixture_data()
        await VolunteerAssignment.create(shift=shift, user=volunteer, auto_generated=True)

        bundle = await VolunteerExportService().build(actor, WINDOW_START, WINDOW_END)
        row = bundle.sheet('assignments').rows[0]

        assert row['stated_availability'] == 'Preferred'
        assert row['source'] == 'Auto-draft'

    async def test_positions_sheet_totals_staffing(self, db):
        actor = await _coordinator()
        _, shift, volunteer = await _fixture_data()
        await VolunteerAssignment.create(shift=shift, user=volunteer)

        bundle = await VolunteerExportService().build(actor, WINDOW_START, WINDOW_END)
        row = bundle.sheet('positions').rows[0]

        assert row['shifts'] == 1
        assert row['slots_needed'] == 2
        assert row['slots_filled'] == 1

    async def test_roster_ignores_the_range_but_the_schedule_respects_it(self, db):
        actor = await _coordinator()
        position, _, _ = await _fixture_data()
        await VolunteerShift.create(
            position=position, starts_at=utc(2026, 12, 1, 12), ends_at=utc(2026, 12, 1, 16),
        )

        bundle = await VolunteerExportService().build(actor, WINDOW_START, WINDOW_END)

        assert [r['name'] for r in bundle.sheet('volunteers').rows] == ['Alex']
        assert len(bundle.sheet('shifts').rows) == 1

    async def test_role_holder_with_nothing_declared_still_exported(self, db):
        actor = await _coordinator()
        await _fixture_data()
        await _person('Casey', roles=[Role.VOLUNTEER])

        bundle = await VolunteerExportService().build(actor, WINDOW_START, WINDOW_END)
        row = next(r for r in bundle.sheet('volunteers').rows if r['name'] == 'Casey')

        assert row['in_volunteer_pool'] is True
        assert row['availability_windows'] == 0
        assert 'opted_in' not in row

    async def test_readme_lists_every_sheet(self, db):
        actor = await _coordinator()
        await _fixture_data()

        bundle = await VolunteerExportService().build(actor, WINDOW_START, WINDOW_END)
        readme = bundle.readme()

        for sheet in bundle.sheets:
            assert f'{sheet.name}.csv' in readme

    async def test_audits_the_export(self, db):
        from models import AuditLog

        actor = await _coordinator()
        await _fixture_data()

        await VolunteerExportService().build(actor, WINDOW_START, WINDOW_END)

        assert await AuditLog.filter(action='volunteer.data_exported').exists()


class TestGuards:
    async def test_non_coordinator_refused(self, db):
        outsider = await _person('nobody')

        with pytest.raises(PermissionError):
            await VolunteerExportService().build(outsider, WINDOW_START, WINDOW_END)

    async def test_staff_allowed(self, db):
        staff = await _person('staff', roles=[Role.STAFF])
        await _fixture_data()

        bundle = await VolunteerExportService().build(staff, WINDOW_START, WINDOW_END)

        assert bundle.sheets

    async def test_inverted_range_rejected(self, db):
        actor = await _coordinator()

        with pytest.raises(ValueError):
            await VolunteerExportService().build(actor, WINDOW_END, WINDOW_START)

    async def test_feature_flag_off_refuses(self, db):
        from application.errors import NotFoundError
        from models import TenantFeatureFlag

        actor = await _coordinator()
        await TenantFeatureFlag.filter(flag='volunteers').update(enabled=False)

        with pytest.raises(NotFoundError):
            await VolunteerExportService().build(actor, WINDOW_START, WINDOW_END)


class TestTenantIsolation:
    async def test_another_tenants_volunteers_never_appear(self, two_tenants):
        tenant_a, tenant_b = two_tenants

        with tenant_scope(tenant_b.id):
            other_position = await VolunteerPosition.create(name='B Desk')
            other_volunteer = await _person('Bea', roles=[Role.VOLUNTEER])
            await VolunteerAvailability.create(
                user=other_volunteer, starts_at=utc(2026, 10, 4, 12), ends_at=utc(2026, 10, 4, 20),
            )
            await VolunteerShift.create(
                position=other_position, starts_at=utc(2026, 10, 4, 12), ends_at=utc(2026, 10, 4, 16),
            )

        with tenant_scope(tenant_a.id):
            actor = await _coordinator()
            await _fixture_data()
            bundle = await VolunteerExportService().build(actor, WINDOW_START, WINDOW_END)

        assert [r['name'] for r in bundle.sheet('volunteers').rows] == ['Alex']
        assert [r['name'] for r in bundle.sheet('positions').rows] == ['Check-in']
        assert len(bundle.sheet('shifts').rows) == 1
