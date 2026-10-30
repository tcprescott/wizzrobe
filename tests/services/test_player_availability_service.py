"""Tests for PlayerAvailabilityService (unit, no DB)."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from application.services.player_availability_service import PlayerAvailabilityService
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
    svc = object.__new__(PlayerAvailabilityService)
    svc.repository = MagicMock()
    svc.audit_service = make_audit_double()
    return svc


class TestCovers:
    def test_available_when_no_windows(self):
        """The whole point of opt-out: silence is a yes."""
        assert PlayerAvailabilityService.covers([], _dt(8), _dt(12)) == \
            VolunteerAvailabilityStatus.AVAILABLE

    def test_available_when_window_overlaps(self):
        windows = [make_window(7, 13)]
        result = PlayerAvailabilityService.covers(windows, _dt(8), _dt(12))
        assert result == VolunteerAvailabilityStatus.AVAILABLE

    def test_preferred_beats_available(self):
        windows = [
            make_window(7, 13, VolunteerAvailabilityStatus.AVAILABLE),
            make_window(8, 12, VolunteerAvailabilityStatus.PREFERRED),
        ]
        result = PlayerAvailabilityService.covers(windows, _dt(8), _dt(12))
        assert result == VolunteerAvailabilityStatus.PREFERRED

    def test_unavailable_wins_outright(self):
        windows = [
            make_window(7, 13, VolunteerAvailabilityStatus.PREFERRED),
            make_window(9, 11, VolunteerAvailabilityStatus.UNAVAILABLE),
        ]
        result = PlayerAvailabilityService.covers(windows, _dt(8), _dt(12))
        assert result == VolunteerAvailabilityStatus.UNAVAILABLE

    def test_blocked_elsewhere_leaves_the_slot_available(self):
        windows = [make_window(14, 18, VolunteerAvailabilityStatus.UNAVAILABLE)]
        result = PlayerAvailabilityService.covers(windows, _dt(8), _dt(12))
        assert result == VolunteerAvailabilityStatus.AVAILABLE

    def test_touching_at_start_is_not_overlap(self):
        windows = [make_window(4, 8, VolunteerAvailabilityStatus.UNAVAILABLE)]
        result = PlayerAvailabilityService.covers(windows, _dt(8), _dt(12))
        assert result == VolunteerAvailabilityStatus.AVAILABLE


class TestEffectiveSegments:
    def test_empty_when_range_is_zero(self):
        result = PlayerAvailabilityService.effective_segments([], _dt(8), _dt(8))
        assert result == []

    def test_single_full_range_available(self):
        windows = [make_window(8, 12)]
        segs = PlayerAvailabilityService.effective_segments(windows, _dt(8), _dt(12))
        assert len(segs) == 1
        assert segs[0][2] == VolunteerAvailabilityStatus.AVAILABLE

    def test_gap_is_available_not_unknown(self):
        windows = [make_window(10, 12, VolunteerAvailabilityStatus.UNAVAILABLE)]
        segs = PlayerAvailabilityService.effective_segments(windows, _dt(8), _dt(12))
        statuses = [s[2] for s in segs]
        assert None not in statuses
        assert statuses == [
            VolunteerAvailabilityStatus.AVAILABLE,
            VolunteerAvailabilityStatus.UNAVAILABLE,
        ]

    def test_whole_range_available_with_no_windows(self):
        segs = PlayerAvailabilityService.effective_segments([], _dt(8), _dt(12))
        assert segs == [(_dt(8), _dt(12), VolunteerAvailabilityStatus.AVAILABLE)]

    def test_contiguous_same_status_merged(self):
        windows = [make_window(8, 10), make_window(10, 12)]
        segs = PlayerAvailabilityService.effective_segments(windows, _dt(8), _dt(12))
        assert len(segs) == 1

    def test_unavailable_creates_own_segment(self):
        windows = [
            make_window(8, 12, VolunteerAvailabilityStatus.AVAILABLE),
            make_window(10, 11, VolunteerAvailabilityStatus.UNAVAILABLE),
        ]
        segs = PlayerAvailabilityService.effective_segments(windows, _dt(8), _dt(12))
        statuses = [s[2] for s in segs]
        assert VolunteerAvailabilityStatus.UNAVAILABLE in statuses


class TestSetWindows:
    async def test_raises_when_window_ends_before_starts(self, service):
        user = SimpleNamespace(id=1)
        bad = (_dt(12), _dt(8), VolunteerAvailabilityStatus.AVAILABLE, None)
        with pytest.raises(ValueError, match='after the start time'):
            await service.set_windows(user, [bad])

    async def test_raises_when_window_ends_equal_to_starts(self, service):
        user = SimpleNamespace(id=1)
        bad = (_dt(8), _dt(8), VolunteerAvailabilityStatus.AVAILABLE, None)
        with pytest.raises(ValueError, match='after the start time'):
            await service.set_windows(user, [bad])


class TestClear:
    async def test_deletes_and_audits(self, service):
        service.repository.delete_for_user = AsyncMock()
        user = SimpleNamespace(id=1)
        await service.clear(user)
        service.repository.delete_for_user.assert_awaited_once_with(user)
        service.audit_service.write_log.assert_awaited_once()


class TestAvailabilityMap:
    async def test_groups_windows_by_user_id(self, service):
        w1 = SimpleNamespace(user_id=1)
        w2 = SimpleNamespace(user_id=2)
        w3 = SimpleNamespace(user_id=1)
        service.repository.for_users_overlapping = AsyncMock(return_value=[w1, w2, w3])
        result = await service.availability_map([1, 2], _dt(8), _dt(16))
        assert len(result[1]) == 2
        assert len(result[2]) == 1

    async def test_returns_empty_map_when_no_rows(self, service):
        service.repository.for_users_overlapping = AsyncMock(return_value=[])
        result = await service.availability_map([1], _dt(8), _dt(16))
        assert result == {}


class TestReminder:
    @pytest.fixture
    def dismissals(self, monkeypatch):
        from application.services import player_availability_service as mod

        repo = SimpleNamespace(dismissed_checks=AsyncMock(return_value=set()), dismiss=AsyncMock(return_value=True))
        monkeypatch.setattr(mod, 'ProfileNudgeRepository', repo)
        return repo

    async def test_shown_to_a_player_with_nothing_blocked(self, service, dismissals):
        service.repository.list_for_user = AsyncMock(return_value=[])
        assert await service.needs_reminder(SimpleNamespace(id=7)) is True

    async def test_hidden_once_anything_is_blocked(self, service, dismissals):
        service.repository.list_for_user = AsyncMock(return_value=[make_window(8, 10)])
        assert await service.needs_reminder(SimpleNamespace(id=7)) is False
        dismissals.dismissed_checks.assert_not_awaited()

    async def test_hidden_for_good_once_put_away(self, service, dismissals):
        from application.services.player_availability_service import REMINDER_KEY

        service.repository.list_for_user = AsyncMock(return_value=[])
        dismissals.dismissed_checks.return_value = {REMINDER_KEY}
        assert await service.needs_reminder(SimpleNamespace(id=7)) is False

    async def test_dismiss_records_the_key(self, service, dismissals):
        from application.services.player_availability_service import REMINDER_KEY

        await service.dismiss_reminder(SimpleNamespace(id=7))
        dismissals.dismiss.assert_awaited_once_with(7, REMINDER_KEY)
