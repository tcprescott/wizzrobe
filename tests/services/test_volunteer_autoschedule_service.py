"""Tests for VolunteerAutoscheduleService (unit, no DB)."""

from collections import Counter
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from application.services.volunteer.volunteer_autoschedule_service import (
    DraftPolicy,
    VolunteerAutoscheduleService,
)
from models import VolunteerAvailabilityStatus
from tests.factories import make_audit_double

UTC = timezone.utc


def _dt(hour, day=4):
    return datetime(2026, 10, day, hour, 0, tzinfo=UTC)


def make_shift(shift_id, starts, ends, position_id=1, slots_needed=1, assignments=None,
               label='Shift 1'):
    return SimpleNamespace(
        id=shift_id,
        position_id=position_id,
        position=SimpleNamespace(name='Check-in'),
        starts_at=starts,
        ends_at=ends,
        slots_needed=slots_needed,
        label=label,
        assignments=assignments or [],
    )


def make_user(uid, name='Alice'):
    return SimpleNamespace(id=uid, preferred_name=name)


def available(*shifts):
    """An availability window covering each of these shifts, keyed for one user."""
    return [
        SimpleNamespace(
            starts_at=s.starts_at, ends_at=s.ends_at,
            status=VolunteerAvailabilityStatus.AVAILABLE,
        )
        for s in shifts
    ]


pytestmark = pytest.mark.usefixtures("bypass_auth")
@pytest.fixture
def service():
    svc = object.__new__(VolunteerAutoscheduleService)
    svc.shift_repository = MagicMock()
    svc.assignment_repository = MagicMock()
    svc.pool_service = MagicMock()
    svc.availability_service = MagicMock()
    svc.schedule_service = MagicMock()
    svc.audit_service = make_audit_double()
    return svc


class TestOverlaps:
    def test_overlapping(self):
        existing = [(_dt(8), _dt(12))]
        assert VolunteerAutoscheduleService._overlaps(existing, _dt(10), _dt(14)) is True

    def test_non_overlapping(self):
        existing = [(_dt(8), _dt(12))]
        assert VolunteerAutoscheduleService._overlaps(existing, _dt(12), _dt(16)) is False

    def test_empty_existing(self):
        assert VolunteerAutoscheduleService._overlaps([], _dt(8), _dt(12)) is False

    def test_adjacent_not_overlapping(self):
        existing = [(_dt(8), _dt(12))]
        assert VolunteerAutoscheduleService._overlaps(existing, _dt(12), _dt(16)) is False


class TestHours:
    def test_four_hour_shift(self):
        assert VolunteerAutoscheduleService._hours(_dt(8), _dt(12)) == 4.0

    def test_zero_when_same_time(self):
        assert VolunteerAutoscheduleService._hours(_dt(8), _dt(8)) == 0.0

    def test_fractional_hours(self):
        start = _dt(8)
        end = start + timedelta(minutes=90)
        assert VolunteerAutoscheduleService._hours(start, end) == 1.5


class TestUnfilledSummary:
    def test_all_filled_returns_empty(self):
        shift = make_shift(1, _dt(8), _dt(12), slots_needed=1, assignments=[SimpleNamespace()])
        result = VolunteerAutoscheduleService._unfilled_summary([shift], {1: 1})
        assert result == []

    def test_unfilled_shift_in_result(self):
        shift = make_shift(1, _dt(8), _dt(12), slots_needed=2, assignments=[])
        result = VolunteerAutoscheduleService._unfilled_summary([shift], {1: 0})
        assert len(result) == 1
        assert result[0]['open'] == 2

    @pytest.mark.parametrize('reason,sentence', [
        (None, 'No qualified volunteer in the pool.'),
        ('not_stated', 'Nobody qualified has marked this time as available.'),
        ('at_hour_cap', 'Everyone eligible is at the 8-hour limit.'),
        ('unavailable', 'Everyone qualified marked this time unavailable.'),
        ('overlapping', 'Everyone qualified is already on another shift.'),
    ])
    def test_says_why_the_slot_stayed_open(self, reason, sentence):
        shift = make_shift(1, _dt(8), _dt(12), slots_needed=1, assignments=[])
        reasons = {1: Counter({reason: 2})} if reason else {}
        result = VolunteerAutoscheduleService._unfilled_summary(
            [shift], {1: 0}, reasons, DraftPolicy(),
        )
        assert result[0]['reason'] == sentence

    def test_dominant_reason_wins(self):
        shift = make_shift(1, _dt(8), _dt(12), slots_needed=1, assignments=[])
        reasons = {1: Counter({'not_stated': 1, 'overlapping': 5})}
        result = VolunteerAutoscheduleService._unfilled_summary(
            [shift], {1: 0}, reasons, DraftPolicy(),
        )
        assert result[0]['reason'] == 'Everyone qualified is already on another shift.'


class TestHeavyLoads:
    def test_names_the_volunteer_on_three_touching_blocks(self):
        heavy = make_user(1, 'Heavy')
        light = make_user(2, 'Light')
        intervals = {
            1: [(_dt(8), _dt(12)), (_dt(12), _dt(16)), (_dt(16), _dt(20))],
            2: [(_dt(8), _dt(12))],
        }
        rows = VolunteerAutoscheduleService._heavy_loads(
            [heavy, light], {1: 12.0, 2: 4.0}, intervals, DraftPolicy(max_hours=0),
        )
        assert [r['name'] for r in rows] == ['Heavy']
        assert rows[0] == {'user_id': 1, 'name': 'Heavy', 'hours': 12.0, 'shifts': 3}

    def test_names_the_volunteer_near_the_hour_ceiling(self):
        user = make_user(1, 'Alice')
        rows = VolunteerAutoscheduleService._heavy_loads(
            [user], {1: 8.0}, {1: [(_dt(8), _dt(12)), (_dt(16), _dt(20))]},
            DraftPolicy(),
        )
        assert rows[0]['hours'] == 8.0

    def test_ignores_a_light_day(self):
        user = make_user(1, 'Alice')
        rows = VolunteerAutoscheduleService._heavy_loads(
            [user], {1: 4.0}, {1: [(_dt(8), _dt(12))]}, DraftPolicy(),
        )
        assert rows == []


class TestPick:
    def test_returns_none_when_pool_empty(self, service):
        shift = make_shift(1, _dt(8), _dt(12))
        result = service._pick(shift, [], [], {}, {}, {}, {}, {})
        assert result is None

    def test_skips_user_already_on_shift(self, service):
        user = make_user(1)
        shift = make_shift(1, _dt(8), _dt(12))
        on_shift = {1: {1}}
        result = service._pick(shift, [user], [1], {}, {}, {1: []}, {1: 0.0}, on_shift)
        assert result is None

    def test_skips_user_with_wrong_qualification(self, service):
        user = make_user(1)
        shift = make_shift(1, _dt(8), _dt(12), position_id=5)
        quals = {1: {99}}  # qualified for position 99, not 5
        on_shift = {1: set()}
        result = service._pick(shift, [user], [1], quals, {}, {1: []}, {1: 0.0}, on_shift)
        assert result is None

    def test_picks_available_user(self, service):
        user = make_user(1)
        shift = make_shift(1, _dt(8), _dt(12))
        on_shift = {1: set()}
        result = service._pick(
            shift, [user], [1], {}, {1: available(shift)}, {1: []}, {1: 0.0}, on_shift,
        )
        assert result is user

    def test_prefers_lower_hours_user(self, service):
        alice = make_user(1, 'Alice')
        bob = make_user(2, 'Bob')
        shift = make_shift(1, _dt(8), _dt(12))
        on_shift = {1: set(), 2: set()}
        # Alice is already at the default 8-hour ceiling, so the cap and the
        # tiebreaker both point at Bob; raise the cap to isolate the tiebreaker.
        hours = {1: 8.0, 2: 0.0}
        result = service._pick(
            shift, [alice, bob], [1, 2], {},
            {1: available(shift), 2: available(shift)},
            {1: [], 2: []}, hours, on_shift,
            policy=DraftPolicy(max_hours=0),
        )
        assert result is bob

    def test_skips_user_who_has_not_stated_availability(self, service):
        user = make_user(1)
        shift = make_shift(1, _dt(8), _dt(12))
        on_shift = {1: set()}
        args = (shift, [user], [1], {}, {}, {1: []}, {1: 0.0}, on_shift)

        assert service._pick(*args) is None
        assert service._pick(
            *args, policy=DraftPolicy(fill_outside_availability=True),
        ) is user

    def test_skips_user_at_the_hour_cap(self, service):
        user = make_user(1)
        shift = make_shift(1, _dt(8), _dt(12))
        on_shift = {1: set()}
        avail = {1: available(shift)}
        # 6h worked + this 4h shift is over the 8h ceiling, even though they are
        # the only qualified, preferred, non-overlapping person in the pool.
        args = (shift, [user], [1], {}, avail, {1: []}, {1: 6.0}, on_shift)

        assert service._pick(*args, shift_hours=4.0) is None
        assert service._pick(
            *args, shift_hours=4.0, policy=DraftPolicy(max_hours=0),
        ) is user

    def test_records_why_each_candidate_was_skipped(self, service):
        shift = make_shift(1, _dt(8), _dt(12))
        undeclared = make_user(1, 'Undeclared')
        refused = make_user(2, 'Refused')
        avail = {2: [SimpleNamespace(
            starts_at=_dt(8), ends_at=_dt(12),
            status=VolunteerAvailabilityStatus.UNAVAILABLE,
        )]}
        reasons = Counter()
        service._pick(
            shift, [undeclared, refused], [1, 2], {}, avail,
            {1: [], 2: []}, {1: 0.0, 2: 0.0}, {1: set(), 2: set()},
            reasons=reasons,
        )
        assert reasons == Counter({'not_stated': 1, 'unavailable': 1})


class TestClearDraft:
    async def test_removes_auto_assignments_and_audits(self, service):
        service.assignment_repository.delete_auto_for_window = AsyncMock(return_value=3)
        removed = await service.clear_draft(actor=MagicMock(), start=_dt(0), end=_dt(23))
        assert removed == 3
        service.audit_service.write_log.assert_awaited_once()


class TestPublishDraft:
    async def test_confirms_each_draft_and_counts_volunteers(self, service):
        drafts = [
            SimpleNamespace(id=1, user_id=10),
            SimpleNamespace(id=2, user_id=10),
            SimpleNamespace(id=3, user_id=11),
        ]
        service.assignment_repository.list_auto_for_window = AsyncMock(return_value=drafts)
        service.schedule_service.confirm_assignment = AsyncMock()

        result = await service.publish_draft(MagicMock(), _dt(0), _dt(23))

        assert result == {'published': 3, 'volunteers': 2, 'clashes': []}
        assert service.schedule_service.confirm_assignment.await_count == 3
        service.audit_service.write_log.assert_awaited_once()

    async def test_empty_window_audits_zero_and_notifies_nobody(self, service):
        service.assignment_repository.list_auto_for_window = AsyncMock(return_value=[])
        service.schedule_service.confirm_assignment = AsyncMock()

        result = await service.publish_draft(MagicMock(), _dt(0), _dt(23))

        assert result == {'published': 0, 'volunteers': 0, 'clashes': []}
        service.schedule_service.confirm_assignment.assert_not_awaited()
        _, args, _kwargs = service.audit_service.write_log.mock_calls[0]
        assert args[2]['published'] == 0
