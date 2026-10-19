"""A player confirming they have seen the match they are scheduled for.

Split out of ``match_service`` the way :class:`MatchReviewMixin` and
:class:`CancellationMixin` are: :class:`MatchAcknowledgmentMixin` is composed
into :class:`MatchService` and reaches ``_require_match``, ``repository``,
``ack_repository``, ``participants`` and ``audit_service`` through that composed
class.

Only a current player of the match may acknowledge, and only once — the second
attempt is a refusal rather than a no-op, because the button that sent it is
telling its clicker something happened. The seeding path is the other half: a
match is scheduled with a row per player already there, so the board can show
who has not answered rather than an empty column.
"""

from typing import List, Optional

from application.events import EventType, match_live
from application.services.audit_service import AuditActions
from models import Match, MatchAcknowledgment, User


class MatchAcknowledgmentMixin:
    """List, record and seed a match's player acknowledgments."""

    async def list_acknowledgments(self, match: Match) -> List[MatchAcknowledgment]:
        return await self.ack_repository.list_for_match(match)

    async def acknowledge_match(self, match_id: int, user: User) -> MatchAcknowledgment:
        """Mark a match as acknowledged by the given player.

        Only current players of the match may acknowledge.
        """
        match = await self._require_match(match_id, prefetch_relations=False)

        players = await self.repository.get_players(match)
        if not any(p.user_id == user.id for p in players):
            raise ValueError("You are not a participant of this match.")

        existing = await self.ack_repository.get(match, user)
        if existing and existing.acknowledged_at is not None:
            raise ValueError("You have already acknowledged this match.")

        ack = await self.ack_repository.upsert(match, user, acknowledged=True, auto=False)
        await self.audit_service.write_and_publish(
            user,
            AuditActions.MATCH_ACKNOWLEDGED,
            {'match_id': match.id, 'tournament_id': match.tournament_id},
            EventType.MATCH_ACKNOWLEDGED,
            event_extra={'user_id': user.id},
        )
        match_live.publish(match.id)
        from application.services import opt_in_notifications
        from application.services.discord import discord_queue
        discord_queue.enqueue(opt_in_notifications.opponent_acknowledged(match.id, user.id))
        return ack

    async def _seed_acknowledgments(
        self,
        match: Match,
        player_ids: List[int],
        actor: Optional[User],
    ) -> None:
        await self.participants.seed_acknowledgments(match, player_ids, actor)
