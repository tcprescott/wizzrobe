"""Tests for VolunteerScheduleService (unit, no DB)."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from application.services.volunteer.volunteer_schedule_service import VolunteerScheduleService
from tests.factories import make_audit_double

UTC = timezone.utc


def _dt(hour, day=4):
    return datetime(2026, 10, day, hour, 0, tzinfo=UTC)


def make_shift(**overrides):
    defaults = dict(
        id=1,
        position_id=1,
        position=SimpleNamespace(name='Check-in'),
        starts_at=_dt(8),
        ends_at=_dt(12),
        slots_needed=1,
        label='Shift 1',
        assignments=[],
        notes=None,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def make_assignment(**overrides):
    defaults = dict(
        id=1, shift_id=1, user_id=42, acknowledged_at=None,
        auto_generated=False, checked_in_at=None, called_off_at=None,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


pytestmark = pytest.mark.usefixtures("bypass_auth")


@pytest.fixture(autouse=True)
def volunteers_feature_live():
    """Treat ``FeatureFlag.VOLUNTEERS`` as live for these DB-less unit tests.

    ``coverage`` and ``day_summary`` are ``@requires_feature``-gated, and the
    guard reads the flag from the database — which these tests do not have. That
    the guard *refuses* without the flag is covered in
    test_feature_flag_enforcement.py against a real bare tenant.
    """
    with patch(
        'application.services.feature_flag_service.FeatureFlagService.ensure_enabled',
        new=AsyncMock(return_value=None),
    ):
        yield


@pytest.fixture
def service():
    svc = object.__new__(VolunteerScheduleService)
    svc.shift_repository = MagicMock()
    svc.assignment_repository = MagicMock()
    svc.position_repository = MagicMock()
    svc.audit_service = make_audit_double()
    svc.discord_service = MagicMock()
    svc.assignment_repository.published_user_ids = AsyncMock(return_value=set())
    svc.assignment_repository.published_unfinished = AsyncMock(return_value=[])
    svc.assignment_repository.overlapping_for_user = AsyncMock(return_value=[])
    svc.position_repository.get_by_id = AsyncMock(
        return_value=SimpleNamespace(id=1, is_staggered=False),
    )
    svc.role_mapping_service = MagicMock(
        reconcile_users=AsyncMock(return_value={}), reconcile_all=AsyncMock(return_value={}),
    )
    return svc


# ---------------------------------------------------------------------------
# create_shift validation
# ---------------------------------------------------------------------------


class TestCreateShift:
    async def test_raises_when_end_before_start(self, service):
        with pytest.raises(ValueError, match='end after'):
            await service.create_shift(
                actor=MagicMock(), position_id=1,
                starts_at=_dt(12), ends_at=_dt(8),
            )

    async def test_raises_when_slots_less_than_one(self, service):
        with pytest.raises(ValueError, match='at least one'):
            await service.create_shift(
                actor=MagicMock(), position_id=1,
                starts_at=_dt(8), ends_at=_dt(12), slots_needed=0,
            )

    async def test_creates_shift_and_audits(self, service):
        shift = make_shift()
        service.shift_repository.create = AsyncMock(return_value=shift)
        result = await service.create_shift(
            actor=MagicMock(), position_id=1,
            starts_at=_dt(8), ends_at=_dt(12),
        )
        assert result is shift
        service.audit_service.write_log.assert_awaited_once()


# ---------------------------------------------------------------------------
# update_shift validation
# ---------------------------------------------------------------------------


class TestUpdateShift:
    async def test_raises_when_end_before_start(self, service):
        shift = make_shift()
        with pytest.raises(ValueError, match='end after'):
            await service.update_shift(
                actor=MagicMock(), shift=shift,
                starts_at=_dt(12), ends_at=_dt(8),
            )

    async def test_raises_when_slots_zero(self, service):
        shift = make_shift()
        with pytest.raises(ValueError, match='at least one'):
            await service.update_shift(
                actor=MagicMock(), shift=shift, slots_needed=0,
            )

    async def test_updates_and_audits(self, service):
        shift = make_shift()
        updated = make_shift(label='New Label')
        service.shift_repository.update = AsyncMock(return_value=updated)
        result = await service.update_shift(
            actor=MagicMock(), shift=shift, label='New Label',
        )
        assert result is updated
        service.audit_service.write_log.assert_awaited_once()


# ---------------------------------------------------------------------------
# delete_shift
# ---------------------------------------------------------------------------


class TestDeleteShift:
    async def test_deletes_and_audits(self, service):
        shift = make_shift(id=7)
        service.shift_repository.delete = AsyncMock()
        await service.delete_shift(actor=MagicMock(), shift=shift)
        service.shift_repository.delete.assert_awaited_once_with(shift)
        service.audit_service.write_log.assert_awaited_once()


# ---------------------------------------------------------------------------
# assign
# ---------------------------------------------------------------------------


class TestAssign:
    @pytest.fixture(autouse=True)
    def _members_only_passes(self):
        # Membership is a DB check of its own (covered against the real tables
        # in test_roster_membership.py); these tests are about what follows it.
        with patch(
            'application.services.volunteer.volunteer_schedule_service.'
            'TenantMembershipService.require_community_users',
            AsyncMock(return_value=None),
        ):
            yield

    async def test_raises_when_already_on_shift(self, service):
        user = SimpleNamespace(id=42, preferred_name='Alice')
        shift = make_shift()
        service.assignment_repository.exists = AsyncMock(return_value=True)
        with pytest.raises(ValueError, match='already on this shift'):
            await service.assign(actor=MagicMock(), shift=shift, user=user)

    async def test_raises_when_overlapping(self, service):
        user = SimpleNamespace(id=42, preferred_name='Alice')
        shift = make_shift()
        service.assignment_repository.exists = AsyncMock(return_value=False)
        service.assignment_repository.overlapping_for_user = AsyncMock(return_value=[make_assignment()])
        with pytest.raises(ValueError, match='overlapping'):
            await service.assign(actor=MagicMock(), shift=shift, user=user)

    async def test_warns_when_overfilled(self, service):
        user = SimpleNamespace(id=42, preferred_name='Alice', discord_id=None, dm_notifications=True)
        shift = make_shift(slots_needed=1, assignments=['existing'])
        service.assignment_repository.exists = AsyncMock(return_value=False)
        service.assignment_repository.overlapping_for_user = AsyncMock(return_value=[])
        service.assignment_repository.create = AsyncMock(return_value=make_assignment())

        with patch.object(service, '_availability_warning', AsyncMock(return_value=None)):
            with patch(
                'application.services.volunteer.volunteer_schedule_service.VolunteerAssignment',
            ) as MockAssn:
                MockAssn.filter.return_value.count = AsyncMock(return_value=2)
                _, warnings = await service.assign(actor=MagicMock(), shift=shift, user=user)
        assert any('slots filled' in w for w in warnings)

    async def test_creates_assignment_and_audits(self, service):
        user = SimpleNamespace(id=42, preferred_name='Alice', discord_id=None, dm_notifications=True)
        shift = make_shift()
        assignment = make_assignment()
        service.assignment_repository.exists = AsyncMock(return_value=False)
        service.assignment_repository.overlapping_for_user = AsyncMock(return_value=[])
        service.assignment_repository.create = AsyncMock(return_value=assignment)

        with patch.object(service, '_availability_warning', AsyncMock(return_value=None)):
            with patch(
                'application.services.volunteer.volunteer_schedule_service.VolunteerAssignment',
            ) as MockAssn:
                MockAssn.filter.return_value.count = AsyncMock(return_value=0)
                result, warnings = await service.assign(actor=MagicMock(), shift=shift, user=user)

        assert result is assignment
        assert warnings == []
        service.audit_service.write_log.assert_awaited_once()


# ---------------------------------------------------------------------------
# acknowledge
# ---------------------------------------------------------------------------


class TestAcknowledge:
    async def test_raises_when_assignment_not_found(self, service):
        service.assignment_repository.get_by_id = AsyncMock(return_value=None)
        with pytest.raises(ValueError, match='not found'):
            await service.acknowledge(assignment_id=999, user=SimpleNamespace(id=1))

    async def test_raises_when_not_owner(self, service):
        assignment = make_assignment(user_id=99)
        service.assignment_repository.get_by_id = AsyncMock(return_value=assignment)
        with pytest.raises(ValueError, match='your own'):
            await service.acknowledge(assignment_id=1, user=SimpleNamespace(id=42))

    async def test_sets_timestamp_and_audits(self, service):
        assignment = make_assignment(user_id=42, acknowledged_at=None)
        service.assignment_repository.get_by_id = AsyncMock(return_value=assignment)
        service.assignment_repository.save = AsyncMock()
        result = await service.acknowledge(assignment_id=1, user=SimpleNamespace(id=42))
        assert result.acknowledged_at is not None
        service.assignment_repository.save.assert_awaited_once()
        service.audit_service.write_log.assert_awaited_once()

    async def test_refuses_a_draft(self, service):
        service.assignment_repository.get_by_id = AsyncMock(
            return_value=make_assignment(auto_generated=True))
        with pytest.raises(ValueError, match='still a draft'):
            await service.acknowledge(1, SimpleNamespace(id=42))

    async def test_idempotent_when_already_acknowledged(self, service):
        ts = datetime.now(UTC)
        assignment = make_assignment(user_id=42, acknowledged_at=ts)
        service.assignment_repository.get_by_id = AsyncMock(return_value=assignment)
        service.assignment_repository.save = AsyncMock()
        result = await service.acknowledge(assignment_id=1, user=SimpleNamespace(id=42))
        assert result.acknowledged_at == ts
        service.assignment_repository.save.assert_not_awaited()


# ---------------------------------------------------------------------------
# release
# ---------------------------------------------------------------------------


def _future_shift(hours_out=48):
    start = datetime.now(UTC) + timedelta(hours=hours_out)
    return make_shift(starts_at=start, ends_at=start + timedelta(hours=4))


def _releasable(service, **overrides):
    overrides.setdefault('shift', _future_shift())
    overrides.setdefault('user_id', 42)
    assignment = make_assignment(**overrides)
    service.assignment_repository.get_by_id = AsyncMock(return_value=assignment)
    service.assignment_repository.delete = AsyncMock()
    service.audit_service.write_and_publish = AsyncMock()
    return assignment


class TestRelease:
    async def test_refuses_someone_elses_assignment(self, service):
        _releasable(service, user_id=99)
        with pytest.raises(ValueError, match='your own'):
            await service.release(1, SimpleNamespace(id=42))

    async def test_refuses_once_checked_in(self, service):
        _releasable(service, checked_in_at=datetime.now(UTC))
        with pytest.raises(ValueError, match='already checked in'):
            await service.release(1, SimpleNamespace(id=42))

    async def test_refuses_a_draft(self, service):
        _releasable(service, auto_generated=True)
        with pytest.raises(ValueError, match='still a draft'):
            await service.release(1, SimpleNamespace(id=42))
        service.assignment_repository.delete.assert_not_awaited()

    async def test_refuses_a_finished_shift(self, service):
        _releasable(service, shift=_future_shift(hours_out=-72))
        with pytest.raises(ValueError, match='already finished'):
            await service.release(1, SimpleNamespace(id=42))

    async def test_deletes_audits_and_notifies_coordinators(self, service):
        assignment = _releasable(service)
        volunteer = SimpleNamespace(id=42, preferred_name='Alice')
        with patch.object(service, '_notify_coordinators_of_release', AsyncMock()) as notify:
            await service.release(1, volunteer, 'Family thing')

        service.assignment_repository.delete.assert_awaited_once_with(assignment)
        notify.assert_awaited_once()
        _, args, _kwargs = service.audit_service.write_and_publish.mock_calls[0]
        assert args[1] == 'volunteer.released'
        assert args[2]['reason'] == 'Family thing'
        assert args[2]['hours_notice'] == pytest.approx(48.0, abs=0.1)
        assert args[3] == 'volunteer.released'

    async def test_whitespace_reason_is_recorded_as_none(self, service):
        _releasable(service)
        with patch.object(service, '_notify_coordinators_of_release', AsyncMock()):
            await service.release(1, SimpleNamespace(id=42, preferred_name='Alice'), '   ')
        _, args, _kwargs = service.audit_service.write_and_publish.mock_calls[0]
        assert args[2]['reason'] is None


# ---------------------------------------------------------------------------
# confirm_assignment
# ---------------------------------------------------------------------------


class TestConfirmAssignment:
    async def test_publishes_notifies_and_publishes_event(self, service):
        assignment = make_assignment(auto_generated=True)
        assignment.shift = make_shift()
        assignment.user = SimpleNamespace(id=42, preferred_name='Alice')
        service.assignment_repository.mark_published = AsyncMock()
        service.audit_service.write_and_publish = AsyncMock()

        with patch.object(service, 'request_acknowledgment', AsyncMock()) as ack:
            await service.confirm_assignment(MagicMock(), assignment)

        service.assignment_repository.mark_published.assert_awaited_once_with(assignment)
        ack.assert_awaited_once()
        _, args, _kwargs = service.audit_service.write_and_publish.mock_calls[0]
        assert args[1] == 'volunteer.assigned'
        assert args[2]['published_from_draft'] is True

    async def test_returns_early_when_already_published(self, service):
        assignment = make_assignment(auto_generated=False)
        service.assignment_repository.mark_published = AsyncMock()
        service.audit_service.write_and_publish = AsyncMock()

        with patch.object(service, 'request_acknowledgment', AsyncMock()) as ack:
            result = await service.confirm_assignment(MagicMock(), assignment)

        assert result is assignment
        service.assignment_repository.mark_published.assert_not_awaited()
        service.audit_service.write_and_publish.assert_not_awaited()
        ack.assert_not_awaited()


# ---------------------------------------------------------------------------
# Notifications: the reverse transitions that used to be silent
# ---------------------------------------------------------------------------


def _coordinator(uid, *, discord_id='900', dm_notifications=True):
    return SimpleNamespace(
        id=uid, preferred_name=f'Coord{uid}',
        discord_id=discord_id, dm_notifications=dm_notifications,
    )


class TestReleaseFanOut:
    async def _run(self, service, recipients_by_role, on_duty=(), can_manage=True):
        async def list_users_with_role(role, active_only=False):
            return recipients_by_role.get(role, [])

        with patch('application.services.staff_alert_service.UserRoleRepository') as repo, \
                patch('application.services.staff_alert_service.StaffAlertService.on_duty_now',
                      AsyncMock(return_value=list(on_duty))), \
                patch('application.services.staff_alert_service.AuthService.can_manage_volunteers',
                      AsyncMock(return_value=can_manage)), \
                patch('application.services.tenant_service.TenantService') as tenants, \
                patch('application.services.discord.discord_queue.enqueue') as enqueue:
            repo.list_users_with_role = AsyncMock(side_effect=list_users_with_role)
            tenants.current_community_name = AsyncMock(return_value='Test Community')
            await service._notify_coordinators_of_release(
                SimpleNamespace(id=42, preferred_name='Alice'),
                make_shift(),
                {'hours_notice': 3.0, 'reason': 'Family thing'},
            )
        return enqueue

    async def test_nobody_on_duty_falls_back_to_all_staff_deduped(self, service):
        from models import Role
        both_roles = _coordinator(1)
        staff_only = _coordinator(2)
        enqueue = await self._run(service, {
            Role.VOLUNTEER_COORDINATOR: [both_roles],
            Role.STAFF: [both_roles, staff_only],
        })
        assert enqueue.call_count == 2

    async def test_on_duty_admin_replaces_all_staff(self, service):
        from models import Role
        on_duty = _coordinator(3)
        on_duty.is_active = True
        enqueue = await self._run(service, {
            Role.VOLUNTEER_COORDINATOR: [_coordinator(1)],
            Role.STAFF: [_coordinator(2), _coordinator(4)],
        }, on_duty=[on_duty])
        # The coordinator and the person on duty; the other staff are spared.
        assert enqueue.call_count == 2

    async def test_on_duty_who_cannot_manage_volunteers_falls_back_to_staff(self, service):
        from models import Role
        on_duty = _coordinator(3)
        on_duty.is_active = True
        enqueue = await self._run(service, {
            Role.STAFF: [_coordinator(2), _coordinator(4)],
        }, on_duty=[on_duty], can_manage=False)
        assert enqueue.call_count == 2

    async def test_skips_coordinators_who_cannot_be_dmed(self, service):
        from models import Role
        enqueue = await self._run(service, {
            Role.VOLUNTEER_COORDINATOR: [_coordinator(1, discord_id=None)],
        })
        enqueue.assert_not_called()

    async def test_sends_nothing_when_nobody_holds_either_role(self, service):
        enqueue = await self._run(service, {})
        enqueue.assert_not_called()


class TestUnassignNotifies:
    async def _unassign(self, service, *, auto_generated):
        assignment = make_assignment(
            auto_generated=auto_generated, shift=make_shift(),
            user=SimpleNamespace(id=42, preferred_name='Alice'),
        )
        service.assignment_repository.get_by_id = AsyncMock(return_value=assignment)
        service.assignment_repository.delete = AsyncMock()
        with patch.object(service, '_notify_removed', AsyncMock()) as notify:
            await service.unassign(MagicMock(), assignment)
        return notify

    async def test_dms_the_volunteer_of_a_published_assignment(self, service):
        (await self._unassign(service, auto_generated=False)).assert_awaited_once()

    async def test_stays_silent_for_a_draft(self, service):
        (await self._unassign(service, auto_generated=True)).assert_not_awaited()


class TestUpdateShiftReasks:
    async def _update(self, service, assignments, **fields):
        shift = make_shift(starts_at=_dt(8), ends_at=_dt(12))
        service.shift_repository.update = AsyncMock(return_value=shift)
        service.assignment_repository.list_for_shift = AsyncMock(return_value=assignments)
        service.assignment_repository.save = AsyncMock()
        with patch.object(service, '_notify_shift_moved', AsyncMock()) as notify:
            await service.update_shift(MagicMock(), shift, **fields)
        return notify

    async def test_a_new_start_clears_acknowledgments_and_re_asks(self, service):
        acked = make_assignment(acknowledged_at=datetime.now(UTC),
                                user=SimpleNamespace(id=42))
        notify = await self._update(service, [acked], starts_at=_dt(16), ends_at=_dt(20))

        assert acked.acknowledged_at is None
        notify.assert_awaited_once()
        _, args, _kwargs = service.audit_service.write_log.mock_calls[0]
        assert args[2]['reacknowledge_cleared'] == 1

    async def test_a_slots_only_edit_notifies_nobody(self, service):
        acked = make_assignment(acknowledged_at=datetime.now(UTC),
                                user=SimpleNamespace(id=42))
        notify = await self._update(service, [acked], slots_needed=3)

        assert acked.acknowledged_at is not None
        notify.assert_not_awaited()
        _, args, _kwargs = service.audit_service.write_log.mock_calls[0]
        assert 'reacknowledge_cleared' not in args[2]

    async def test_a_moved_draft_is_left_alone(self, service):
        draft = make_assignment(auto_generated=True, acknowledged_at=None,
                                user=SimpleNamespace(id=42))
        notify = await self._update(service, [draft], starts_at=_dt(16), ends_at=_dt(20))
        notify.assert_not_awaited()


# ---------------------------------------------------------------------------
# coverage
# ---------------------------------------------------------------------------


class TestCoverage:
    async def test_reports_understaffed_shift(self, service):
        shift = make_shift(slots_needed=2, assignments=[make_assignment()])
        shift.position = SimpleNamespace(name='Proctor')
        service.shift_repository.list_for_window = AsyncMock(return_value=[shift])
        rows = await service.coverage(_dt(0), _dt(23))
        assert len(rows) == 1
        assert rows[0]['filled'] == 1
        assert rows[0]['needed'] == 2
        assert rows[0]['understaffed'] is True

    async def test_reports_fully_staffed_shift(self, service):
        shift = make_shift(slots_needed=1, assignments=[make_assignment()])
        service.shift_repository.list_for_window = AsyncMock(return_value=[shift])
        rows = await service.coverage(_dt(0), _dt(23))
        assert rows[0]['understaffed'] is False

    async def test_breaks_the_filled_total_down_by_state(self, service):
        shift = make_shift(slots_needed=3, assignments=[
            make_assignment(id=1, auto_generated=True),
            make_assignment(id=2, acknowledged_at=datetime.now(UTC)),
            make_assignment(id=3),
        ])
        service.shift_repository.list_for_window = AsyncMock(return_value=[shift])
        rows = await service.coverage(_dt(0), _dt(23))
        assert rows[0]['filled'] == 3          # drafts count for the coordinator
        assert rows[0]['drafts'] == 1
        assert rows[0]['acknowledged'] == 1


class TestDaySummary:
    async def test_aggregates_the_days_states(self, service):
        morning = make_shift(id=1, slots_needed=2, assignments=[
            make_assignment(id=1, acknowledged_at=datetime.now(UTC)),
            make_assignment(id=2, auto_generated=True),
        ])
        afternoon = make_shift(id=2, slots_needed=2, assignments=[
            make_assignment(id=3),
        ])
        service.shift_repository.list_for_window = AsyncMock(
            return_value=[morning, afternoon])

        summary = await service.day_summary(_dt(0), _dt(23))

        assert summary == {
            'shifts': 2, 'needed': 4, 'filled': 3, 'open': 1,
            'drafts': 1, 'unacknowledged': 1, 'understaffed_shifts': 1,
        }

    async def test_an_empty_day_reports_zeroes(self, service):
        service.shift_repository.list_for_window = AsyncMock(return_value=[])
        summary = await service.day_summary(_dt(0), _dt(23))
        assert summary['shifts'] == 0 and summary['open'] == 0
