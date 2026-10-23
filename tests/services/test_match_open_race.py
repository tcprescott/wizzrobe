"""Open races: more than four players, no winner, and Finish confirms them."""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from application.services.match.match_schedule_service import MatchScheduleService
from application.services.notification_catalog import MATCH_CONFIRMED
from tests.factories import make_audit_double
from tests.services.test_match_schedule_service import MockMatch, mock_players

pytestmark = pytest.mark.usefixtures("bypass_auth")


@pytest.fixture
def service():
    svc = object.__new__(MatchScheduleService)
    svc.match_repository = MagicMock()
    svc.match_repository.stamp_if_unset = AsyncMock(return_value=True)
    svc.audit_service = make_audit_double()
    svc.notify_match_participants = AsyncMock()
    return svc


class TestFinishOpenRace:
    """More than four players: no winner is recorded, and Finish confirms it."""

    @staticmethod
    def _live(count):
        now = datetime.now()
        names = [f'P{i}' for i in range(count)]
        return MockMatch(seated_at=now, started_at=now, players=mock_players(*names))

    async def test_five_players_finish_and_confirm(self, service):
        match = self._live(5)
        await service.finish_match(match)
        assert match.finished_at is not None
        assert match.confirmed_at is not None

    async def test_only_the_confirmed_dm_goes_out(self, service):
        await service.finish_match(self._live(7))
        notices = [c.kwargs['notice'] for c in service.notify_match_participants.call_args_list]
        assert notices == [MATCH_CONFIRMED]

    async def test_four_players_still_wait_for_a_winner(self, service):
        match = self._live(4)
        await service.finish_match(match)
        assert match.finished_at is not None
        assert match.confirmed_at is None

    async def test_closed_out_on_the_finish_gate_not_the_confirm_gate(self, service, monkeypatch):
        """A proctor may finish but not confirm; an open race still closes out."""
        from application.services.auth_service import AuthService
        can_run = AsyncMock(return_value=True)
        can_confirm = AsyncMock(return_value=False)
        monkeypatch.setattr(AuthService, 'can_run_match', can_run)
        monkeypatch.setattr(AuthService, 'can_confirm_match', can_confirm)
        match = self._live(5)
        await service.finish_match(match)
        assert match.confirmed_at is not None
        assert can_run.await_count == 2
        can_confirm.assert_not_awaited()

    async def test_confirm_accepts_an_open_race_without_a_result(self, service):
        match = self._live(5)
        match.finished_at = datetime.now()
        await service.confirm_match(match)
        assert match.confirmed_at is not None
