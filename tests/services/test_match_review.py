"""The dispute flag's authorization split and its resolution on confirm (T4.4).

Real roles, no ``bypass_auth``: the whole point of this feature is *who* may do
*what*. Raising the flag is the proctor's (``can_run_match``) and dropping it is
the admin's (``can_confirm_match``), so a bypassed gate would test nothing. The
service-level behaviour that needs no roles — the finished-match guard, note
trimming, the event — lives in ``test_match_service.py``.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from application.repositories import MatchAcknowledgmentRepository, MatchRepository
from application.services import MatchService
from application.services.audit_service import AuditService
from application.services.match.match_schedule_service import MatchScheduleService
from application.services.seedgen_service import SeedGenerationService
from models import AuditLog, Match, MatchPlayers, Role, Tournament, User, UserRole
from tests.factories import utc


async def make_user(discord_id, *, name="u"):
    return await User.create(
        discord_id=discord_id, username=name, display_name=name.upper(),
    )


async def make_staff(discord_id=9000):
    user = await make_user(discord_id, name="staff")
    await UserRole.create(user=user, role=Role.STAFF)
    return user


async def make_proctor(discord_id=9001):
    user = await make_user(discord_id, name="proctor")
    await UserRole.create(user=user, role=Role.PROCTOR)
    return user


async def record_winner(player: MatchPlayers):
    """Stamp the winning rank ``confirm_match`` insists on having seen."""
    player.finish_rank = 1
    await player.save()
    return player


@pytest.fixture
def service():
    """A MatchScheduleService with real repositories/audit and a stubbed Discord."""
    svc = object.__new__(MatchScheduleService)
    svc.match_repository = MatchRepository()
    svc.acknowledgment_repository = MatchAcknowledgmentRepository()
    svc.seedgen_service = SeedGenerationService()
    svc.audit_service = AuditService()
    discord = MagicMock()
    discord.send_dm = AsyncMock(return_value=(True, "ok"))
    discord.send_dm_with_unwatch_button = AsyncMock(return_value=(True, "ok"))
    discord.send_dm_with_acknowledgment_button = AsyncMock(return_value=(True, "ok"))
    discord.send_dm_with_crew_buttons = AsyncMock(return_value=(True, "ok"))
    svc.discord_service = discord
    return svc


class TestConfirmingResolvesTheReviewFlag:
    """Confirming a flagged result *is* the resolution (T4.4).

    Real roles, no auth bypass: the split matters here, because raising the flag
    is the proctor's and dropping it is the admin's — the proctor must not be
    able to unflag their own dispute, and the admin never has to press anything
    extra to resolve it.
    """

    async def _flagged_match(self, service, proctor):
        t = await Tournament.create(name="T")
        m = await Match.create(tournament=t, scheduled_at=utc(2025, 1, 15, 19, 30))
        await MatchPlayers.create(match=m, user=await make_user(1, name="p1"))
        await MatchPlayers.create(match=m, user=await make_user(2, name="p2"))
        await service.seat_match(m, proctor)
        await service.start_match(m, proctor)
        await service.finish_match(m, proctor)
        await record_winner(await MatchPlayers.filter(match_id=m.id).first())
        await MatchService().flag_for_review(m.id, proctor)
        await m.refresh_from_db()
        return m

    async def test_proctor_can_flag_but_not_clear(self, service, db):
        proctor = await make_proctor()
        m = await self._flagged_match(service, proctor)
        assert m.needs_review is True

        with pytest.raises(PermissionError):
            await MatchService().clear_review(m.id, proctor)

        await m.refresh_from_db()
        assert m.needs_review is True

    async def test_staff_can_clear(self, service, db):
        proctor = await make_proctor()
        staff = await make_staff()
        m = await self._flagged_match(service, proctor)

        await MatchService().clear_review(m.id, staff)

        await m.refresh_from_db()
        assert m.needs_review is False

    async def test_confirming_clears_the_flag(self, service, db):
        proctor = await make_proctor()
        staff = await make_staff()
        m = await self._flagged_match(service, proctor)

        await service.confirm_match(m, staff)

        await m.refresh_from_db()
        assert m.confirmed_at is not None
        assert m.needs_review is False

    async def test_confirming_audits_the_resolution(self, service, db):
        """The audit row is the record of the dispute — confirming resolved it."""
        proctor = await make_proctor()
        staff = await make_staff()
        m = await self._flagged_match(service, proctor)

        await service.confirm_match(m, staff)

        cleared = await AuditLog.filter(action='match.review_cleared').all()
        assert len(cleared) == 1
        assert "confirmation" in (cleared[0].details or '')

    async def test_confirming_an_unflagged_match_writes_no_clear_row(self, service, db):
        """The common case stays a single confirm row — no phantom resolution."""
        staff = await make_staff()
        t = await Tournament.create(name="T")
        m = await Match.create(tournament=t, scheduled_at=utc(2025, 1, 15, 19, 30))
        await MatchPlayers.create(match=m, user=await make_user(1, name="p1"))
        await service.seat_match(m, staff)
        await service.start_match(m, staff)
        await service.finish_match(m, staff)
        await record_winner(await MatchPlayers.filter(match_id=m.id).first())

        await service.confirm_match(m, staff)

        assert await AuditLog.filter(action='match.review_cleared').count() == 0


class TestAConfirmedResultIsTheAdminsToChange:
    """Confirming is the admin's verification step and has already pushed the
    result on, so a proctor (who may record a winner but not confirm one) must
    not be able to rewrite a confirmed winner, nor reopen it with a flag."""

    async def _confirmed_match(self, service):
        staff = await make_staff()
        t = await Tournament.create(name="T")
        m = await Match.create(tournament=t, scheduled_at=utc(2025, 1, 15, 19, 30))
        winner = await MatchPlayers.create(match=m, user=await make_user(1, name="p1"))
        loser = await MatchPlayers.create(match=m, user=await make_user(2, name="p2"))
        await service.seat_match(m, staff)
        await service.start_match(m, staff)
        await service.finish_match(m, staff)
        await MatchService().record_match_result(m.id, winner.id, staff)
        await service.confirm_match(m, staff)
        return m, winner, loser, staff

    async def test_proctor_cannot_rewrite_a_confirmed_winner(self, service, db):
        m, winner, loser, _staff = await self._confirmed_match(service)
        proctor = await make_proctor()

        with pytest.raises(PermissionError, match="confirmed"):
            await MatchService().record_match_result(m.id, loser.id, proctor)

        await winner.refresh_from_db()
        assert winner.finish_rank == 1

    async def test_staff_can_correct_a_confirmed_winner(self, service, db):
        m, _winner, loser, staff = await self._confirmed_match(service)

        await MatchService().record_match_result(m.id, loser.id, staff)

        await loser.refresh_from_db()
        assert loser.finish_rank == 1

    async def test_proctor_can_still_record_before_confirmation(self, service, db):
        proctor = await make_proctor()
        t = await Tournament.create(name="T")
        m = await Match.create(tournament=t, scheduled_at=utc(2025, 1, 15, 19, 30))
        p1 = await MatchPlayers.create(match=m, user=await make_user(1, name="p1"))
        await MatchPlayers.create(match=m, user=await make_user(2, name="p2"))

        await MatchService().record_match_result(m.id, p1.id, proctor)

        await p1.refresh_from_db()
        assert p1.finish_rank == 1

    async def test_a_confirmed_match_cannot_be_flagged(self, service, db):
        m, *_ = await self._confirmed_match(service)
        proctor = await make_proctor()

        with pytest.raises(ValueError, match="already confirmed"):
            await MatchService().flag_for_review(m.id, proctor)

        await m.refresh_from_db()
        assert m.needs_review is False
