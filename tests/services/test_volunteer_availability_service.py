"""Tests for VolunteerAvailabilityService (unit, no DB)."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from application.services.volunteer.volunteer_availability_service import VolunteerAvailabilityService
from models import VolunteerAvailabilityStatus
from tests.factories import make_audit_double

UTC = timezone.utc


def _dt(hour, minute=0):
    return datetime(2026, 10, 4, hour, minute, tzinfo=UTC)


def make_window(starts_hour, ends_hour, status=VolunteerAvailabilityStatus.AVAILABLE, note=None):
    return SimpleNamespace(
        starts_at=_dt(starts_hour),
        ends_at=_dt(ends_hour),
        status=status,
        note=note,
        user_id=1,
    )


@pytest.fixture
def service():
    svc = object.__new__(VolunteerAvailabilityService)
    svc.repository = MagicMock()
    svc.audit_service = make_audit_double()
    return svc


class TestCovers:
    def test_returns_none_when_no_windows(self):
        assert VolunteerAvailabilityService.covers([], _dt(8), _dt(12)) is None

    def test_available_when_window_overlaps(self):
        windows = [make_window(7, 13)]
        result = VolunteerAvailabilityService.covers(windows, _dt(8), _dt(12))
        assert result == VolunteerAvailabilityStatus.AVAILABLE

    def test_preferred_beats_available(self):
        windows = [
            make_window(7, 13, VolunteerAvailabilityStatus.AVAILABLE),
            make_window(8, 12, VolunteerAvailabilityStatus.PREFERRED),
        ]
        result = VolunteerAvailabilityService.covers(windows, _dt(8), _dt(12))
        assert result == VolunteerAvailabilityStatus.PREFERRED

    def test_unavailable_wins_outright(self):
        windows = [
            make_window(7, 13, VolunteerAvailabilityStatus.PREFERRED),
            make_window(9, 11, VolunteerAvailabilityStatus.UNAVAILABLE),
        ]
        result = VolunteerAvailabilityService.covers(windows, _dt(8), _dt(12))
        assert result == VolunteerAvailabilityStatus.UNAVAILABLE

    def test_non_overlapping_window_ignored(self):
        windows = [make_window(14, 18)]
        result = VolunteerAvailabilityService.covers(windows, _dt(8), _dt(12))
        assert result is None

    def test_adjacent_window_not_counted(self):
        # ends_at == start => no overlap
        windows = [make_window(4, 8)]
        result = VolunteerAvailabilityService.covers(windows, _dt(8), _dt(12))
        assert result is None


class TestEffectiveSegments:
    def test_empty_when_end_lte_start(self):
        result = VolunteerAvailabilityService.effective_segments([], _dt(8), _dt(8))
        assert result == []

    def test_single_available_window_full_range(self):
        windows = [make_window(8, 12)]
        segs = VolunteerAvailabilityService.effective_segments(windows, _dt(8), _dt(12))
        assert len(segs) == 1
        assert segs[0][2] == VolunteerAvailabilityStatus.AVAILABLE

    def test_gap_produces_none_segment(self):
        windows = [make_window(10, 12)]
        segs = VolunteerAvailabilityService.effective_segments(windows, _dt(8), _dt(12))
        statuses = [s[2] for s in segs]
        assert None in statuses

    def test_adjacent_same_status_merged(self):
        windows = [
            make_window(8, 10),
            make_window(10, 12),
        ]
        segs = VolunteerAvailabilityService.effective_segments(windows, _dt(8), _dt(12))
        assert len(segs) == 1
        assert segs[0][2] == VolunteerAvailabilityStatus.AVAILABLE

    def test_unavailable_splits_segment(self):
        windows = [
            make_window(8, 12, VolunteerAvailabilityStatus.AVAILABLE),
            make_window(10, 11, VolunteerAvailabilityStatus.UNAVAILABLE),
        ]
        segs = VolunteerAvailabilityService.effective_segments(windows, _dt(8), _dt(12))
        statuses = [s[2] for s in segs]
        assert VolunteerAvailabilityStatus.UNAVAILABLE in statuses


class TestSetWindows:
    async def test_raises_when_window_end_before_start(self, service):
        user = SimpleNamespace(id=1)
        bad_window = (_dt(12), _dt(8), VolunteerAvailabilityStatus.AVAILABLE, None)
        with pytest.raises(ValueError, match='end after'):
            await service.set_windows(user, [bad_window])


class TestClear:
    async def test_deletes_and_audits(self, service):
        service.repository.delete_for_user = AsyncMock()
        user = SimpleNamespace(id=1)
        await service.clear(user)
        service.repository.delete_for_user.assert_awaited_once_with(user)
        service.audit_service.write_log.assert_awaited_once()


class TestAvailabilityMap:
    async def test_groups_by_user_id(self, service):
        w1 = SimpleNamespace(user_id=1, starts_at=_dt(8), ends_at=_dt(12))
        w2 = SimpleNamespace(user_id=2, starts_at=_dt(8), ends_at=_dt(12))
        w3 = SimpleNamespace(user_id=1, starts_at=_dt(12), ends_at=_dt(16))
        service.repository.for_users_overlapping = AsyncMock(return_value=[w1, w2, w3])
        result = await service.availability_map([1, 2], _dt(8), _dt(16))
        assert len(result[1]) == 2
        assert len(result[2]) == 1
