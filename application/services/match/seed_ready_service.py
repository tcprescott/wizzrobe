"""Players marking themselves ready for their seed.

On a tournament with ``players_release_seed`` on, once a proctor checks the
match in each player taps Ready for seed (My Schedule, or the button on their
check-in DM). The tap that makes everyone ready rolls the seed, and rolling is
what sends it: the usual "Seed ready" DM goes out and the seed shows on their
match. A player can take Ready back until then. Proctors and staff still roll
whenever they like through ``MatchScheduleService.generate_seed``.

The first time a player is ready, every player who isn't gets a quiet DM saying
so, with the same button. Once per player per match, so toggling Ready can't
be used to ping the opponent.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from application.errors import require_found
from application.events import EventType, match_live
from application.repositories import MatchRepository, ProviderTaskRepository
from application.repositories._save import save_fields
from application.services.audit_service import AuditActions, AuditService
from application.services.match.seed_ready import ready_count, seed_ready_open
from models import Match, MatchPlayers, User

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SeedReadyResult:
    """Where the player stands after a tap, and what to tell them."""

    ready: bool
    released: bool
    message: str
    ok: bool = True


class SeedReadyService:
    """Set and take back a player's Ready for seed."""

    def __init__(self) -> None:
        self.repository = MatchRepository()
        self.audit_service = AuditService()

    async def _own_row(self, match_id: int, user: User) -> tuple[Match, MatchPlayers]:
        match = require_found(await self.repository.get_by_id(match_id), f"Match {match_id}")
        for player in match.players:  # type: ignore[attr-defined]
            if player.user_id == user.id:
                return match, player
        raise ValueError('Only the players in a match can say they are ready for its seed.')

    @staticmethod
    def _closed_reason(match: Match) -> str:
        tournament = match.tournament
        if getattr(match, 'generated_seed_id', None) is not None:
            return 'Your seed is already out. Open your match to get it.'
        if tournament is None or not tournament.players_release_seed or tournament.is_racetime_enabled:
            return 'A proctor rolls the seed for this match.'
        if match.finished_at is not None:
            return 'This match is already finished.'
        return "You can say you're ready once a proctor checks your match in."

    async def set_ready(self, match_id: int, user: User) -> SeedReadyResult:
        """Mark ``user`` ready; the last player to do so releases the seed.

        Tapping again while already ready retries the release, which is the way
        back after a roll that failed.
        """
        match, player = await self._own_row(match_id, user)
        if not seed_ready_open(match):
            raise ValueError(self._closed_reason(match))
        if await ProviderTaskRepository.active_for_match(match.id) is not None:
            return SeedReadyResult(True, True, 'Your seed is rolling. It arrives in a few minutes.')

        if player.seed_ready_at is None:
            player.seed_ready_at = datetime.now(timezone.utc)
            await save_fields(player, 'seed_ready_at')
            await self.audit_service.write_and_publish(
                user, AuditActions.MATCH_PLAYER_SEED_READY,
                {'match_id': match.id},
                EventType.MATCH_PLAYER_SEED_READY,
                event_extra={'tournament_id': match.tournament_id},
            )
            match_live.publish(match.id)

        ready, total = ready_count(match)
        if ready < total:
            await self._nudge_others(match, player, user)
            waiting = [p.user.preferred_name for p in match.players if p.seed_ready_at is None]  # type: ignore[attr-defined]
            return SeedReadyResult(
                True, False,
                f"You're ready. Your seed comes as soon as {_names(waiting)} "
                f"{'is' if len(waiting) == 1 else 'are'} too.",
            )
        return await self._release(match, user)

    async def set_not_ready(self, match_id: int, user: User) -> SeedReadyResult:
        """Take Ready back, until the seed has gone out."""
        match, player = await self._own_row(match_id, user)
        if getattr(match, 'generated_seed_id', None) is not None:
            raise ValueError('Your seed is already out, so Ready can no longer be taken back.')
        if await ProviderTaskRepository.active_for_match(match.id) is not None:
            raise ValueError('Your seed is already rolling, so Ready can no longer be taken back.')
        if player.seed_ready_at is None:
            return SeedReadyResult(False, False, "You're not marked ready.")
        player.seed_ready_at = None  # type: ignore[assignment]
        await save_fields(player, 'seed_ready_at')
        await self.audit_service.write_and_publish(
            user, AuditActions.MATCH_PLAYER_SEED_NOT_READY,
            {'match_id': match.id},
            EventType.MATCH_PLAYER_SEED_NOT_READY,
            event_extra={'tournament_id': match.tournament_id},  # type: ignore[attr-defined]
        )
        match_live.publish(match.id)
        return SeedReadyResult(False, False, "Okay, you're not ready. Tap Ready when you are.")

    async def _release(self, match: Match, user: User) -> SeedReadyResult:
        from application.services.match.match_schedule_service import MatchScheduleService

        ok, message, _ = await MatchScheduleService().release_seed_to_ready_players(match.id, user)
        if ok:
            match_live.publish(match.id)
            return SeedReadyResult(True, True, "Everyone's ready. Your seed is on its way.")
        if 'already' in message:
            return SeedReadyResult(True, True, "Everyone's ready. Your seed is on its way.")
        return SeedReadyResult(
            True, False,
            f"Everyone's ready, but the seed didn't roll: {message} "
            "Tap Ready again to retry, or ask a proctor.",
            ok=False,
        )

    async def _nudge_others(self, match: Match, player: MatchPlayers, user: User) -> None:
        """Tell the players who aren't ready, the first time this one is."""
        if player.seed_nudged_at is not None:
            return
        waiting = [p for p in match.players if p.seed_ready_at is None and p.user.discord_id]
        player.seed_nudged_at = datetime.now(timezone.utc)
        await save_fields(player, 'seed_nudged_at')
        if not waiting:
            return
        from application.services.discord import discord_queue
        from application.services.match._seed_ready_notifications import notify_opponent_seed_ready
        from application.services.match.match_schedule_service import _community_name

        discord_queue.enqueue(notify_opponent_seed_ready(
            match_id=match.id,
            tournament_name=match.tournament.name if match.tournament else '',
            community_name=await _community_name(),
            ready_name=user.preferred_name,
            player_names=[p.user.preferred_name for p in match.players],
            when=match.scheduled_at,
            stage_name=match.stage.name if match.stage else None,
            recipient_ids=[p.user.discord_id for p in waiting],
        ))


def _names(names: list[str]) -> str:
    if len(names) <= 1:
        return names[0] if names else 'everyone else'
    return ', '.join(names[:-1]) + ' and ' + names[-1]
