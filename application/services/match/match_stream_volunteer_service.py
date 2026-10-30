"""Players offering their own match for stream.

A signal, not a request queue. A player says "we're happy to be on stream" and
staff see it while they build the stream schedule — that is the whole feature.
It deliberately does **not** set ``Match.is_stream_candidate``: deciding what
goes out is ``can_assign_match_stream``'s job, and a player's offer must not be
able to write the decision it is asking about. Every surface that shows a
volunteer says so, because a control that looks like it books a stream slot and
does not is worse than no control.

Only the match's own players may volunteer it, and only before it starts —
after that there is nothing left for staff to schedule.
"""

from typing import Dict, List

from application.errors import require_found
from application.events import EventType
from application.repositories import MatchRepository, MatchStreamVolunteerRepository
from application.services.audit_service import AuditActions, AuditService
from models import Match, MatchStreamVolunteer, User


class MatchStreamVolunteerService:
    """Service for players volunteering their match for stream coverage."""

    def __init__(self) -> None:
        self.repository = MatchStreamVolunteerRepository()
        self.match_repository = MatchRepository()
        self.audit_service = AuditService()

    async def _require_playable_match(self, match_id: int, user: User) -> Match:
        """The match, if ``user`` plays in it and it has not started yet."""
        match = require_found(
            await self.match_repository.get_by_id(match_id),
            f"Match {match_id}",
        )
        players = match.players  # type: ignore[attr-defined]
        if not any(p.user_id == user.id for p in players):
            raise ValueError('Only the players in a match can offer it for stream.')
        return match

    async def volunteer(self, match_id: int, user: User) -> MatchStreamVolunteer:
        match = await self._require_playable_match(match_id, user)
        if match.started_at is not None:
            raise ValueError('This match has already started.')

        row, created = await self.repository.get_or_create(match=match, user=user)
        if created:
            await self.audit_service.write_and_publish(
                user,
                AuditActions.MATCH_STREAM_VOLUNTEERED,
                {'match_id': match_id},
                EventType.MATCH_STREAM_VOLUNTEERED,
                event_extra={'tournament_id': match.tournament_id},
            )
            from application.services import opt_in_notifications
            from application.services.discord import discord_queue
            discord_queue.enqueue(opt_in_notifications.stream_offer(match_id, user.id, user.preferred_name))
        return row

    async def withdraw(self, match_id: int, user: User) -> bool:
        """Take the offer back. Allowed after the match starts — a player who
        changes their mind should not be stuck with a request they can't unsay."""
        match = await self._require_playable_match(match_id, user)
        removed = await self.repository.delete_by_match_and_user(match=match, user=user)
        if removed:
            await self.audit_service.write_and_publish(
                user,
                AuditActions.MATCH_STREAM_VOLUNTEER_WITHDRAWN,
                {'match_id': match_id},
                EventType.MATCH_STREAM_VOLUNTEER_WITHDRAWN,
                event_extra={'tournament_id': match.tournament_id},  # type: ignore[attr-defined]
            )
        return removed

    async def has_volunteered(self, match_id: int, user: User) -> bool:
        return await self.repository.is_volunteer(match_id, user.id)

    async def list_volunteered_match_ids(self, user: User) -> List[int]:
        return await self.repository.get_match_ids_for_user(user)

    async def names_by_match(self, match_ids: List[int]) -> Dict[int, List[str]]:
        """``{match_id: [name, ...]}`` for a board, in one query."""
        return await self.repository.names_by_match(list(match_ids))

    async def names_for_match(self, match: Match) -> List[str]:
        rows = await self.repository.get_by_match(match)
        return [row.user.preferred_name for row in rows]
