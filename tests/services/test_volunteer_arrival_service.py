"""Tests for VolunteerArrivalService and the shift-start copy (unit, no DB)."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from application.services.volunteer.volunteer_arrival_service import (
    VolunteerArrivalService,
    can_self_check_in,
    can_undo_check_in,
    describe_late,
)
from application.utils.discord_messages_volunteer import (
    quoted_reason,
    released_description,
    released_notice,
)
from tests.factories import make_audit_double

UTC = timezone.utc


def _assignment(*, starts_in_min=10, length_min=120, **overrides):
    now = datetime.now(UTC)
    shift = SimpleNamespace(
        starts_at=now + timedelta(minutes=starts_in_min),
        ends_at=now + timedelta(minutes=starts_in_min + length_min),
        position=SimpleNamespace(name='Race Proctor'), label=None,
    )
    defaults = dict(
        id=7, shift_id=3, shift=shift, user_id=42, auto_generated=False,
        checked_in_at=None, checked_in_by_id=None, late_minutes=None, called_off_at=None,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


VOLUNTEER = SimpleNamespace(id=42, preferred_name='Alice')


@pytest.fixture(autouse=True)
def volunteers_feature_live():
    with patch(
        'application.services.feature_flag_service.FeatureFlagService.ensure_enabled',
        new=AsyncMock(return_value=None),
    ):
        yield


@pytest.fixture
def service():
    svc = object.__new__(VolunteerArrivalService)
    svc.assignment_repository = MagicMock()
    svc.assignment_repository.save = AsyncMock()
    svc.audit_service = make_audit_double()
    return svc


def _load(service, assignment):
    service.assignment_repository.get_by_id = AsyncMock(return_value=assignment)


class TestWindow:
    def test_opens_thirty_minutes_before(self):
        now = datetime.now(UTC)
        assert can_self_check_in(_assignment(starts_in_min=29), now)
        assert not can_self_check_in(_assignment(starts_in_min=31), now)

    def test_stays_open_until_the_shift_ends(self):
        now = datetime.now(UTC)
        assert can_self_check_in(_assignment(starts_in_min=-60, length_min=120), now)
        assert not can_self_check_in(_assignment(starts_in_min=-120, length_min=60), now)

    def test_never_for_a_draft_or_a_done_check_in(self):
        now = datetime.now(UTC)
        assert not can_self_check_in(_assignment(auto_generated=True), now)
        assert not can_self_check_in(_assignment(checked_in_at=now), now)


class TestCheckInSelf:
    async def test_records_the_volunteer_as_their_own_checker(self, service):
        assignment = _assignment(late_minutes=10)
        _load(service, assignment)
        await service.check_in_self(7, VOLUNTEER)
        assert assignment.checked_in_at is not None
        assert assignment.checked_in_by_id == 42
        assert assignment.late_minutes is None
        _, args, _ = service.audit_service.write_log.mock_calls[0]
        assert args[1] == 'volunteer.checked_in'
        assert args[2]['self_reported'] is True

    async def test_refuses_someone_elses_shift(self, service):
        _load(service, _assignment(user_id=99))
        with pytest.raises(ValueError, match='your own'):
            await service.check_in_self(7, VOLUNTEER)

    async def test_refuses_before_the_window(self, service):
        _load(service, _assignment(starts_in_min=90))
        with pytest.raises(ValueError, match='30 minutes before'):
            await service.check_in_self(7, VOLUNTEER)
        service.assignment_repository.save.assert_not_awaited()

    async def test_refuses_a_draft(self, service):
        _load(service, _assignment(auto_generated=True))
        with pytest.raises(ValueError, match='draft'):
            await service.check_in_self(7, VOLUNTEER)

    async def test_repeat_is_a_no_op(self, service):
        _load(service, _assignment(checked_in_at=datetime.now(UTC), checked_in_by_id=42))
        await service.check_in_self(7, VOLUNTEER)
        service.assignment_repository.save.assert_not_awaited()
        service.audit_service.write_log.assert_not_awaited()


class TestUndo:
    async def test_clears_their_own_check_in(self, service):
        assignment = _assignment(checked_in_at=datetime.now(UTC), checked_in_by_id=42)
        _load(service, assignment)
        await service.undo_check_in(7, VOLUNTEER)
        assert assignment.checked_in_at is None
        _, args, _ = service.audit_service.write_log.mock_calls[0]
        assert args[1] == 'volunteer.check_in_undone'

    async def test_cannot_undo_a_coordinators_check_in(self, service):
        _load(service, _assignment(checked_in_at=datetime.now(UTC), checked_in_by_id=5))
        with pytest.raises(ValueError, match='coordinator'):
            await service.undo_check_in(7, VOLUNTEER)

    def test_undo_offered_only_on_their_own(self):
        now = datetime.now(UTC)
        assert can_undo_check_in(_assignment(checked_in_at=now, checked_in_by_id=42), VOLUNTEER, now)
        assert not can_undo_check_in(_assignment(checked_in_at=now, checked_in_by_id=5), VOLUNTEER, now)


class TestRunningLate:
    async def test_records_and_tells_the_coordinators(self, service):
        assignment = _assignment(starts_in_min=-5)
        _load(service, assignment)
        with patch.object(service, '_notify_coordinators_of_late', AsyncMock()) as notify:
            await service.report_late(7, VOLUNTEER, 20)
        assert assignment.late_minutes == 20
        notify.assert_awaited_once()
        assert describe_late(assignment) == 'Running late (about 20 minutes)'

    async def test_same_answer_twice_does_not_notify_again(self, service):
        _load(service, _assignment(late_minutes=20))
        with patch.object(service, '_notify_coordinators_of_late', AsyncMock()) as notify:
            await service.report_late(7, VOLUNTEER, 20)
        notify.assert_not_awaited()

    async def test_only_the_offered_choices(self, service):
        _load(service, _assignment())
        with pytest.raises(ValueError):
            await service.report_late(7, VOLUNTEER, 45)

    async def test_not_once_checked_in(self, service):
        _load(service, _assignment(checked_in_at=datetime.now(UTC)))
        with pytest.raises(ValueError, match='already checked in'):
            await service.report_late(7, VOLUNTEER, 10)


class TestCoordinatorCopy:
    def test_reason_reaches_the_embed(self):
        text = released_description('Alice', -0.1, 'Car broke down')
        assert 'Car broke down' in text
        assert 'after the shift started' in text

    def test_reason_is_escaped_and_capped(self):
        said = quoted_reason('**bold** @everyone ' + 'x' * 400)
        assert '\\*\\*bold\\*\\*' in said
        assert '@everyone' not in said
        assert len(said) < 340

    def test_blank_reason_is_dropped(self):
        assert quoted_reason('   ') is None

    def test_notice_wording(self):
        assert released_notice(None) is None
        assert released_notice(0.4) == 'Notice: less than an hour.'
        assert released_notice(3.2) == 'Notice: about 3 hours.'
