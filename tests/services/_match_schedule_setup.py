"""The service double and row helpers the MatchScheduleService tests share.

Three modules cover this service — ``test_match_schedule_service`` (the
lifecycle transitions and message builders), ``test_match_schedule_coverage``
(seeding and the confirm/Challonge push) and
``test_match_schedule_notifications`` (the fan-out helpers) — and the last two
were one file until it crossed the 800-line guideline. The fixture lives here so
the split did not fork it: a stub that drifts between two halves of the same
suite is worse than the length it was meant to fix.
"""

from unittest.mock import AsyncMock, MagicMock

from application.repositories import MatchAcknowledgmentRepository, MatchRepository
from application.services.audit_service import AuditService
from application.services.match.match_hard_preset_service import MatchHardPresetService
from application.services.match.match_schedule_service import MatchScheduleService
from application.services.seedgen_service import SeedGenerationService
from models import MatchPlayers, Role, User, UserRole
from tests.factories import make_user


def build_service() -> MatchScheduleService:
    """A MatchScheduleService with real repositories/audit but a stubbed Discord
    service (so DM sends are recorded, never dispatched) and a real
    SeedGenerationService whose network call is monkeypatched per test."""
    svc = object.__new__(MatchScheduleService)
    svc.match_repository = MatchRepository()
    svc.acknowledgment_repository = MatchAcknowledgmentRepository()
    svc.seedgen_service = SeedGenerationService()
    svc.audit_service = AuditService()
    svc.hard_preset_service = MatchHardPresetService()
    discord = MagicMock()
    discord.send_dm = AsyncMock(return_value=(True, "ok"))
    discord.send_dm_with_unwatch_button = AsyncMock(return_value=(True, "ok"))
    discord.send_dm_with_acknowledgment_button = AsyncMock(return_value=(True, "ok"))
    discord.send_dm_with_crew_buttons = AsyncMock(return_value=(True, "ok"))
    svc.discord_service = discord
    return svc


async def make_dm_user(discord_id, *, name="u", dm=True) -> User:
    """A user with a display name and an explicit DM opt-in.

    Every notifier under test branches on ``dm_notifications``, and the DM text
    is built from ``preferred_name``, so both have to be set deliberately rather
    than left on the model default. The row itself comes from the shared
    ``tests.factories`` user factory.
    """
    return await make_user(
        discord_id, name, display_name=name.upper(), dm_notifications=dm,
    )


async def make_staff(discord_id=9000):
    user = await make_dm_user(discord_id, name="staff")
    await UserRole.create(user=user, role=Role.STAFF)
    return user


async def make_proctor(discord_id=9001):
    user = await make_dm_user(discord_id, name="proctor")
    await UserRole.create(user=user, role=Role.PROCTOR)
    return user


async def record_winner(player: MatchPlayers):
    """Stamp the winning rank ``confirm_match`` insists on having seen."""
    player.finish_rank = 1
    await player.save()
    return player
