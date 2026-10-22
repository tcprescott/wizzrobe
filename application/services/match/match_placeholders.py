"""Bracket bookings made before the players are known, mixed into ``MatchService``.

Staff schedule late elimination rounds themselves, for staging, while the
feeder matches are still being played. Such a booking starts with the players
the bracket already knows (often none) and a placeholder per empty side
(``Match.bracket_placeholders``, e.g. "Andy or Benny"). Each bracket sync calls
:meth:`fill_bracket_players` with the matchup's current state: players who are
now known join the match and get the normal "match scheduled" DM, and the
placeholders for the sides still empty are refreshed.

A fill only adds. A matchup that now names someone else than a player already
on the match (a corrected result on the bracket site) is flagged with
``bracket_link_lost`` for staff instead of rewriting the roster.
"""

from typing import Any, List, Optional

from application.events import EventType, match_live
from application.services.audit_service import AuditActions
from application.services.discord import discord_queue
from application.services.match._dm_context import bracket_line_for
from models import Match, User


class PlaceholderBookingMixin:
    repository: Any
    ack_repository: Any
    audit_service: Any
    match_schedule_service: Any
    participants: Any

    async def fill_bracket_players(
        self, match: Match, player_ids: List[Optional[int]], placeholders: List[str], actor: User,
    ) -> bool:
        """Bring a placeholder booking up to date with its matchup; True if anything changed.

        ``player_ids`` are the matchup's sides that resolve to an account (None
        for the rest) and ``placeholders`` the labels for every side still
        without one. A match that already started is left as it is.
        """
        if match.bracket_placeholders is None or match.started_at is not None:
            return False
        current = {p.user_id for p in await self.repository.get_players(match)}
        wanted = {uid for uid in player_ids if uid is not None}
        if current - wanted and len(wanted) == len(player_ids):
            # Every side is decided and someone on the match isn't one of them.
            if not match.bracket_link_lost:
                await self.repository.update(match, bracket_link_lost=True)
                match_live.publish(match.id)
                return True
            return False
        to_add = sorted(wanted - current)
        labels = list(placeholders) or None
        if not to_add and labels == match.bracket_placeholders and not match.bracket_link_lost:
            return False

        if to_add:
            users = await self.participants.resolve_users(to_add)
            await self._record_auto_enrolments(
                await self.participants.ensure_enrolled(match.tournament_id, users),
                match.tournament_id, actor,
            )
            for user in users:
                await self.repository.add_player(match, user)
                await self.ack_repository.upsert(match, user, acknowledged=False, auto=False)
        await self.repository.update(match, bracket_placeholders=labels, bracket_link_lost=False)

        if to_add:
            await self.audit_service.write_and_publish(
                actor, AuditActions.MATCH_UPDATED,
                {'match_id': match.id, 'changed_fields': ['players'],
                 'player_ids': sorted(current | set(to_add)), 'filled_from_bracket': True},
                EventType.MATCH_UPDATED,
                event_details={'match_id': match.id, 'tournament_id': match.tournament_id,
                               'changed_fields': ['players']},
            )
            from application.services.tenant_service import TenantService
            community = await TenantService.current_community_name()
            discord_queue.enqueue(self.match_schedule_service.notify_acknowledgment_request(
                match, rescheduled=False, community=community,
                bracket_line=await bracket_line_for(match.id),
            ))
        match_live.publish(match.id)
        return True

    async def mark_bracket_link_lost(self, match_ids: List[int]) -> None:
        """Flag placeholder bookings whose matchup the bracket site no longer has."""
        for match in await self.repository.get_by_ids_with_players(match_ids):
            if match.bracket_placeholders is not None and not match.bracket_link_lost:
                await self.repository.update(match, bracket_link_lost=True)
                match_live.publish(match.id)

    async def lost_placeholder_bookings(self, tournament_ids: List[int]) -> List[Match]:
        """Placeholder bookings a bracket reset cut loose, for staff to re-link."""
        return [m for m in await self.repository.bracket_link_candidates(tournament_ids) if m.bracket_link_lost]

    async def require_placeholder_relinkable(
        self, match_id: int, tournament_id: int, player_ids: List[Optional[int]],
    ) -> Match:
        """Load a cut-loose placeholder booking that may back this matchup, else raise."""
        candidates = await self.repository.bracket_link_candidates([tournament_id])
        match = next((m for m in candidates if m.id == match_id), None)
        if match is None or match.bracket_placeholders is None:
            raise ValueError(
                "That booking can't be linked: it's in another tournament, already linked "
                "to a bracket, or already confirmed."
            )
        if {p.user_id for p in match.players} - {uid for uid in player_ids if uid is not None}:
            raise ValueError("That booking already has a player who isn't in this matchup.")
        return match
