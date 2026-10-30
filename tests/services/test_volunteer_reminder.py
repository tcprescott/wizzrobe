"""Tests for volunteer_reminder module (unit, no DB)."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import application.services.volunteer.volunteer_reminder as reminder_mod


class TestStartStop:
    # start/stop now delegate to the shared BackgroundLoop (application/utils/
    # background_loop.py); the task lives at reminder_mod._loop._task, so these
    # target and reset that rather than the old module-global _task.
    async def test_start_creates_task(self):
        reminder_mod._loop._task = None
        loop = asyncio.get_event_loop()
        with patch.object(loop, 'create_task', return_value=MagicMock()) as mock_create:
            reminder_mod.start()
        mock_create.assert_called_once()
        reminder_mod._loop._task = None

    async def test_start_is_idempotent(self):
        reminder_mod._loop._task = MagicMock()
        loop = asyncio.get_event_loop()
        with patch.object(loop, 'create_task') as mock_create:
            reminder_mod.start()
        mock_create.assert_not_called()
        reminder_mod._loop._task = None

    async def test_stop_when_no_task_is_noop(self):
        reminder_mod._loop._task = None
        await reminder_mod.stop()  # must not raise

    async def test_stop_cancels_task(self):
        task = asyncio.ensure_future(asyncio.sleep(100))
        reminder_mod._loop._task = task
        await reminder_mod.stop()
        assert task.cancelled()
        assert reminder_mod._loop._task is None


def test_tick_seconds_is_positive():
    assert reminder_mod.TICK_SECONDS > 0


async def test_tick_noop_when_no_due_assignments(monkeypatch):
    """_tick should do nothing when the repository returns an empty list."""

    async def fake_due(now, window_end):
        return []

    monkeypatch.setattr(
        'application.repositories.VolunteerAssignmentRepository.due_for_reminder',
        fake_due,
        raising=False,
    )
    with patch('application.services.volunteer.volunteer_reminder._tick', AsyncMock(return_value=None)):
        pass  # just confirm the patch mechanism works

    with patch(
        'application.repositories.VolunteerAssignmentRepository',
    ) as MockRepo:
        MockRepo.due_for_reminder = AsyncMock(return_value=[])
        MockRepo.due_for_start_dm = AsyncMock(return_value=[])
        with patch(
            'application.services.system_config_service.SystemConfigService.get_volunteer_reminder_lead_minutes',
            AsyncMock(return_value=60),
        ):
            await reminder_mod._tick()
