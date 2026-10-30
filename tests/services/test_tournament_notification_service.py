"""Unit tests for TournamentNotificationService.

Validates the enum gate (rejects invalid notification levels), the
tournament-existence check, and delegation to the repository's upsert.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from application.services.tournament_notification_service import TournamentNotificationService
from models import MatchNotificationLevel


@pytest.fixture
def service():
    svc = object.__new__(TournamentNotificationService)
    svc.repository = MagicMock()
    svc.repository.get_by_user_and_tournament = AsyncMock()
    svc.repository.get_all_for_user = AsyncMock(return_value=[])
    svc.repository.upsert = AsyncMock()
    svc.tournament_repository = MagicMock()
    svc.tournament_repository.get_by_id = AsyncMock()
    return svc


def make_user(user_id=1):
    return SimpleNamespace(id=user_id)


def make_tournament(tournament_id=1):
    return SimpleNamespace(id=tournament_id, name='Cup')


class TestGetPreference:
    async def test_returns_none_when_tournament_missing(self, service):
        service.tournament_repository.get_by_id = AsyncMock(return_value=None)
        result = await service.get_preference(make_user(), tournament_id=42)
        assert result is None
        service.repository.get_by_user_and_tournament.assert_not_called()

    async def test_delegates_when_tournament_present(self, service):
        t = make_tournament(7)
        service.tournament_repository.get_by_id = AsyncMock(return_value=t)
        sentinel = object()
        service.repository.get_by_user_and_tournament = AsyncMock(return_value=sentinel)
        user = make_user()
        result = await service.get_preference(user, tournament_id=7)
        assert result is sentinel
        service.repository.get_by_user_and_tournament.assert_awaited_once_with(user, t)


class TestGetUserPreferences:
    async def test_delegates(self, service):
        sentinel = [object(), object()]
        service.repository.get_all_for_user = AsyncMock(return_value=sentinel)
        user = make_user()
        result = await service.get_user_preferences(user)
        assert result is sentinel
        service.repository.get_all_for_user.assert_awaited_once_with(user)


class TestUpsertPreference:
    async def test_rejects_invalid_notification_level(self, service):
        with pytest.raises(ValueError, match='Invalid notification level'):
            await service.upsert_preference(
                make_user(), tournament_id=1, match_notifications='shout-out',
            )
        service.tournament_repository.get_by_id.assert_not_called()
        service.repository.upsert.assert_not_called()

    async def test_error_message_lists_valid_levels(self, service):
        with pytest.raises(ValueError) as exc:
            await service.upsert_preference(
                make_user(), tournament_id=1, match_notifications='invalid',
            )
        msg = str(exc.value)
        for level in MatchNotificationLevel:
            assert level.value in msg

    @pytest.mark.parametrize('level', [l.value for l in MatchNotificationLevel])
    async def test_accepts_every_enum_value(self, service, level):
        service.tournament_repository.get_by_id = AsyncMock(return_value=make_tournament())
        await service.upsert_preference(
            make_user(), tournament_id=1, match_notifications=level,
        )
        service.repository.upsert.assert_awaited_once()

    async def test_raises_when_tournament_missing(self, service):
        service.tournament_repository.get_by_id = AsyncMock(return_value=None)
        with pytest.raises(ValueError, match='not found'):
            await service.upsert_preference(
                make_user(),
                tournament_id=999,
                match_notifications=MatchNotificationLevel.ALL.value,
            )
        service.repository.upsert.assert_not_called()

    async def test_happy_path_passes_enum_instance_to_repository(self, service):
        t = make_tournament(5)
        service.tournament_repository.get_by_id = AsyncMock(return_value=t)
        user = make_user(99)

        await service.upsert_preference(
            user,
            tournament_id=5,
            match_notifications=MatchNotificationLevel.STREAMED.value,
        )

        service.repository.upsert.assert_awaited_once()
        kwargs = service.repository.upsert.await_args.kwargs
        assert kwargs['user'] is user
        assert kwargs['tournament'] is t
        # The service converts the raw string to an enum instance before storing.
        assert kwargs['match_notifications'] == MatchNotificationLevel.STREAMED
        assert isinstance(kwargs['match_notifications'], MatchNotificationLevel)
