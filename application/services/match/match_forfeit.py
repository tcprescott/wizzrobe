"""Deciding a match by forfeit: a no-show, a withdrawal or a DQ.

Composed into :class:`MatchService`. A forfeit is one step where the floor
lifecycle is four (check in, start, finish, confirm), so it stamps whatever of
those is still empty and confirms in the same call. The players hear about it
once, from a forfeit DM, instead of once per step.

The result is stored the way a race room records a DQ: the winner ranked 1, the
player who forfeited left without a rank. ``Match.forfeit_reason`` says why.
"""

from datetime import datetime, timezone
from typing import Optional

from application.events import EventType, match_live
from application.repositories._save import save_fields
from application.services.audit_service import AuditActions
from application.services.auth_service import AuthService
from application.services.discord import discord_queue
from application.services.match.bracket_result_guard import assert_bracket_result_editable
from application.services.notification_catalog import MATCH_FORFEITED
from models import ForfeitReason, Match, MatchPlayers, User

NOTE_MAX = 280


class ForfeitMixin:
    """``MatchService`` methods for forfeits. Uses the composed service's repos."""

    async def forfeit_match(
        self,
        match_id: int,
        forfeiting_player_id: int,
        reason: ForfeitReason,
        actor: User,
        *,
        note: str = '',
    ) -> Match:
        """Decide a head-to-head match against one player and confirm it.

        ``forfeiting_player_id`` is a :class:`MatchPlayers` row id, as
        ``record_match_result`` takes. Staff or the tournament's admin only:
        confirming pushes the result to the bracket.
        """
        match = await self._require_match(match_id)  # type: ignore[attr-defined]
        await AuthService.ensure(
            await AuthService.can_confirm_match(actor, match),
            "Only staff or the tournament admin can record a forfeit.",
        )
        forfeiter = next((p for p in match.players if p.id == forfeiting_player_id), None)
        if forfeiter is None:
            raise ValueError("That player isn't part of this match.")
        await self._forfeit(match, forfeiter, reason, actor, note=note)
        return match

    async def _forfeit(
        self,
        match: Match,
        forfeiter: MatchPlayers,
        reason: ForfeitReason,
        actor: User,
        *,
        note: str = '',
        tell_forfeiter: bool = True,
    ) -> MatchPlayers:
        """The forfeit itself, after the caller's gate. Returns the winner's row.

        ``tell_forfeiter=False`` leaves the forfeiting player out of the DM, for
        a drop from the tournament that sends them one message of its own.
        """
        from application.services.bracket_service import BracketService

        await match.refresh_from_db(fields=(
            'seated_at', 'started_at', 'finished_at', 'confirmed_at', 'needs_review',
        ))
        if match.confirmed_at:
            raise ValueError(
                "This match is already confirmed. Rewind it first if the result is wrong."
            )
        players = list(match.players)  # type: ignore[attr-defined]
        if len(players) != 2:
            raise ValueError("A forfeit needs a match with exactly two players.")
        note = (note or '').strip()
        if len(note) > NOTE_MAX:
            raise ValueError(f"Keep the note under {NOTE_MAX} characters.")
        assert_bracket_result_editable(await BracketService().get_game_for_match(match.id))

        winner = next(p for p in players if p.id != forfeiter.id)
        winner.finish_rank = 1
        winner.finish_time = None
        forfeiter.finish_rank = None
        forfeiter.finish_time = None
        await save_fields(winner, 'finish_rank', 'finish_time')
        await save_fields(forfeiter, 'finish_rank', 'finish_time')

        now = datetime.now(timezone.utc)
        for field in ('seated_at', 'started_at', 'finished_at'):
            if getattr(match, field) is None and await self.repository.stamp_if_unset(match, field, now):  # type: ignore[attr-defined]
                setattr(match, field, now)
        if not await self.repository.stamp_if_unset(match, 'confirmed_at', now):  # type: ignore[attr-defined]
            raise ValueError("Someone else just confirmed this match. Refresh to see where it stands.")
        match.confirmed_at = now
        match.forfeit_reason = reason
        match.forfeit_note = note or None
        match.needs_review = False
        await save_fields(match, 'forfeit_reason', 'forfeit_note', 'needs_review')

        await self._cancel_race_room(match, actor, f'forfeit: {reason.label}')

        await match.fetch_related('tournament', 'players__user', 'stage')
        await self.audit_service.write_and_publish(  # type: ignore[attr-defined]
            actor,
            AuditActions.MATCH_FORFEITED,
            {
                'match_id': match.id,
                'winner_id': winner.id,
                'forfeited_player_id': forfeiter.id,
                'forfeited_user_id': forfeiter.user_id,  # type: ignore[attr-defined]
                'reason': reason.value,
                'note': note or None,
            },
            EventType.MATCH_FORFEITED,
            event_extra={'tournament_id': match.tournament_id, 'tournament': match.tournament.name},  # type: ignore[attr-defined]
        )
        await self._notify_forfeit(match, winner, forfeiter, reason, tell_forfeiter=tell_forfeiter)

        from application.services import opt_in_notifications
        discord_queue.enqueue(opt_in_notifications.match_result(match.id))
        self.match_schedule_service.push_confirmed_result(match, actor)  # type: ignore[attr-defined]
        match_live.publish(match.id)
        return winner

    async def _notify_forfeit(
        self, match: Match, winner: MatchPlayers, forfeiter: MatchPlayers,
        reason: ForfeitReason, *, tell_forfeiter: bool,
    ) -> None:
        from application.services.match.match_schedule_service import (
            _community_name,
            _match_descriptor,
            _match_embed_kwargs,
            bracket_line_for,
        )
        from application.utils.discord_embeds import COLOR_CONFIRMED, match_embed
        from application.utils.discord_messages_forfeit import forfeit_description, forfeit_dm

        community = await _community_name()
        bracket_line = await bracket_line_for(match.id)
        names = {
            'winner_name': winner.user.preferred_name,  # type: ignore[attr-defined]
            'forfeiter_name': forfeiter.user.preferred_name,  # type: ignore[attr-defined]
            'reason_label': reason.label,
        }

        def copy(you: str = '') -> tuple:
            text = forfeit_dm(
                match.tournament.name, **names, you=you,  # type: ignore[attr-defined]
                **_match_descriptor(match, bracket_line),
            )
            card = match_embed(
                title='🏳️ Match forfeited', color=COLOR_CONFIRMED,
                description=forfeit_description(**names, you=you),
                **_match_embed_kwargs(match, community),
            )
            return text, card

        message, embed = copy()
        copies = {}
        for player, you in ((winner, 'winner'), (forfeiter, 'forfeiter')):
            discord_id = player.user.discord_id  # type: ignore[attr-defined]
            if discord_id:
                copies[discord_id] = copy(you)
        skip = frozenset()
        if not tell_forfeiter and forfeiter.user.discord_id:  # type: ignore[attr-defined]
            skip = frozenset({forfeiter.user.discord_id})  # type: ignore[attr-defined]
        discord_queue.enqueue(self.match_schedule_service.notify_match_participants(  # type: ignore[attr-defined]
            match, message, embed, notice=MATCH_FORFEITED, player_copies=copies,
            skip_discord_ids=skip,
        ))


def forfeiter_of(match: Match) -> Optional[MatchPlayers]:
    """The player a forfeited match went against, from loaded ``players``."""
    if match.forfeit_reason is None:
        return None
    return next((p for p in match.players if p.finish_rank is None), None)  # type: ignore[attr-defined]
