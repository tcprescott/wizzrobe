"""Match cancellation — calling a scheduled match off, and telling people.

Lifted out of :class:`MatchService` (the file-length guideline) as
``MatchParticipants`` was, but as a **mixin** rather than a helper object,
because these methods reach the service's ``_remove_match``, ``audit_service``,
and ``match_schedule_service`` — the same composition ``BracketService`` uses for
``application/services/_bracket/``.

Cancellation is deliberately distinct from deletion. ``delete_match`` is the
staff "this shouldn't exist" path and notifies nobody; cancelling is "this was
real, people committed to it, and it is now off" — so it closes the racetime
room, DMs the players and crew, and emits ``match.cancelled`` alongside the
``match.deleted`` every existing subscriber already listens for.
"""

import logging
from typing import Optional

from application.events import EventType, match_live
from application.services.audit_service import AuditActions
from application.services.auth_service import AuthService
from application.services.discord import discord_queue
from application.services.notification_catalog import MATCH_CANCELLED, Notice
from models import Match, User

logger = logging.getLogger(__name__)


class CancellationMixin:
    """Remove a match: the plain delete, and the notifying cancellation."""

    async def delete_match(self, match_id: int, actor: Optional[User] = None) -> None:
        match = await self._require_match(match_id)
        await AuthService.ensure(
            await AuthService.can_crud_match(actor, match),
            f"User cannot delete match {match_id}",
        )
        await self._remove_match(match, actor)

    async def _remove_match(
        self, match: Match, actor: Optional[User], *, reason: str = '',
    ) -> None:
        """Delete the row, audit, and publish — the one deletion implementation.

        Shared by :meth:`delete_match` and :meth:`cancel_match` so a cancellation
        still emits ``match.deleted`` for existing subscribers. Cleanup of the
        child rows (players, acknowledgments, crew, watchers) is DB-level cascade.

        The bracket slot is released **first**, while the match still exists:
        ``BracketMatchGame.match`` is SET_NULL, so once the row is gone the game
        is unreachable from it (D3, and the same ordering constraint the
        cancellation DM fan-out below documents). Sitting here rather than in
        :meth:`_cancel_match` covers the plain staff delete too — the slot is
        just as stranded either way.
        """
        match_id = match.id
        tournament_id = match.tournament_id
        await self._release_bracket_slot(match, actor, reason)
        await self.repository.delete(match)
        await self.audit_service.write_and_publish(
            actor, AuditActions.MATCH_DELETED, {'match_id': match_id},
            EventType.MATCH_DELETED, event_extra={'tournament_id': tournament_id},
        )
        match_live.publish(match_id, match_live.DELETED)

    @staticmethod
    async def _release_bracket_slot(
        match: Match, actor: Optional[User], reason: str,
    ) -> None:
        """Hand this match's bracket game slot back, if it holds one. Best-effort.

        Service→service, the same shape ``confirm_match`` uses to reach
        ``advance_if_linked``. A no-op for the overwhelming majority of matches,
        which back no bracket game — and for a game that already ``COMPLETE``d or
        was ``CANCELLED`` by a clinch, which
        :meth:`BracketService.release_game_if_linked` skips.

        Swallowing failures is deliberate: a bracket problem must not leave a
        match the staff asked to cancel half-cancelled. The stranded slot is
        recoverable (``scripts/release_orphaned_bracket_games.py``); a
        half-deleted match is not.
        """
        from application.services.bracket_service import BracketService

        try:
            await BracketService().release_game_if_linked(
                match, actor, reason=reason,
            )
        except Exception:
            logger.exception('bracket slot release failed for match %s', match.id)

    async def cancel_match(
        self, match_id: int, actor: Optional[User] = None, *, reason: str = '',
        notice: Notice = MATCH_CANCELLED,
    ) -> None:
        """Call a scheduled match off, telling everyone who was expecting it."""
        match = await self._require_match(match_id)
        await AuthService.ensure(
            await AuthService.can_crud_match(actor, match),
            f"User cannot cancel match {match_id}",
        )
        await self._cancel_match(match, actor, reason=reason, notice=notice)

    async def _cancel_match(
        self, match: Match, actor: Optional[User], *, reason: str = '',
        notice: Notice = MATCH_CANCELLED,
    ) -> None:
        """Cancel a match — WITHOUT the CRUD gate.

        The un-gated core of :meth:`cancel_match`, mirroring the
        ``report_result`` / ``_record_result`` split. A best-of-N series that
        clinches early cancels its remaining pre-scheduled games as the **system
        user**, which holds no per-tenant STAFF role and so cannot pass
        ``can_crud_match``.

        Ordering is load-bearing. Recipients and the message are resolved *first*,
        while the match still exists, because the DM is fanned out later on
        ``discord_queue`` and every recipient query cascades away with the row
        (see :meth:`MatchScheduleService.notify_match_cancelled`). The racetime
        room is cancelled *before* the delete too, while it can still resolve its
        match — otherwise SET_NULL orphans it in OPEN status forever, invisible to
        ``get_by_match``.
        """
        from application.services.match.match_schedule_service import (
            _community_name,
            _match_descriptor,
            _match_embed_kwargs,
            bracket_dm_context,
            collect_match_audience,
        )
        from application.utils.discord_embeds import COLOR_CANCELLED, match_embed
        from application.utils.discord_messages import cancelled_dm

        await match.fetch_related('tournament', 'players__user', 'stage')
        recipients = await collect_match_audience(match)
        community = await _community_name()
        # Both bracket facts are resolved here, before the delete, for the same
        # reason the recipients are: the game row is reachable from this match
        # only while it exists. ``frees_slot`` is what turns "nothing further is
        # needed from you" into "the matchup is open to reschedule" (D3).
        bracket_line, frees_slot = await bracket_dm_context(match.id)
        message = cancelled_dm(
            match.tournament.name, reason=reason, released=frees_slot,
            **_match_descriptor(match, bracket_line),
        )
        embed = match_embed(
            title='Match cancelled',
            color=COLOR_CANCELLED,
            **_match_embed_kwargs(match, community),
        )

        await self._cancel_race_room(match, actor, reason)

        details = {
            'match_id': match.id, 'tournament_id': match.tournament_id,
            'reason': reason or None,
        }
        await self.audit_service.write_and_publish(
            actor, AuditActions.MATCH_CANCELLED, details, EventType.MATCH_CANCELLED,
        )
        await self._remove_match(match, actor, reason=reason)

        discord_queue.enqueue(
            self.match_schedule_service.notify_match_cancelled(
                recipients, message, embed, notice=notice,
            )
        )

    @staticmethod
    async def _cancel_race_room(
        match: Match, actor: Optional[User], reason: str
    ) -> None:
        """Close the match's racetime room, if it has a live one. Best-effort."""
        from application.repositories import RacetimeRoomRepository
        from application.services.race_room_service import RaceRoomService
        from models import RaceRoomStatus

        try:
            room = await RacetimeRoomRepository().get_by_match(match)
            if room is None or room.status not in (
                RaceRoomStatus.OPEN, RaceRoomStatus.IN_PROGRESS,
            ):
                return
            await RaceRoomService().cancel_room(
                room, actor=actor, reason=reason or 'match cancelled',
            )
        except Exception:
            logger.exception('race room cancel failed for match %s', match.id)
