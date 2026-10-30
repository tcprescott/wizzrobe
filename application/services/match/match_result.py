"""Recording who won a match by hand.

Composed into :class:`MatchService`. A match's first result also tells its
tournament's admins (:mod:`application.services.result_alerts`); a correction
doesn't, since they already heard.
"""

from typing import Dict

from application.events import EventType, match_live
from application.repositories._save import save_fields
from application.services import result_alerts
from application.services.audit_service import AuditActions
from application.services.auth_service import AuthService
from application.services.discord import discord_queue
from application.services.match.bracket_result_guard import assert_bracket_result_editable
from application.services.match.match_status import has_recorded_result
from models import Match, User


class RecordResultMixin:
    async def record_match_result(
        self,
        match_id: int,
        winner_id: int,
        actor: User,
        *,
        announce: bool = True,
    ) -> Match:
        """Record finish ranks for a 2-player match.

        Winner gets rank 1; remaining player gets rank 2. The ``winner_id``
        is a :class:`MatchPlayers` row id, not a User id. A match's first result
        DMs its tournament's admins; ``announce=False`` is for a result copied
        from a bracket site, whose sync already told them.
        """
        match = await self._require_match(match_id)  # type: ignore[attr-defined]

        await AuthService.ensure(
            await AuthService.can_run_match(actor, match),
            f"User cannot record results for match {match_id}",
        )
        if match.confirmed_at:
            await AuthService.ensure(
                await AuthService.can_confirm_match(actor, match),
                "This result is confirmed. Only staff or the tournament admin can change it.",
            )

        if not match.players:
            raise ValueError("Match has no players")

        if not any(p.id == winner_id for p in match.players):
            raise ValueError("That player isn't part of this match.")

        # A settled bracket game is corrected from the bracket; rewriting ranks here desyncs it.
        from application.services.bracket_service import BracketService

        assert_bracket_result_editable(
            await BracketService().get_game_for_match(match.id)
        )

        first_result = not has_recorded_result(match.players)
        ranks: Dict[int, int] = {}
        for player in match.players:
            player.finish_rank = 1 if player.id == winner_id else 2
            await save_fields(player, 'finish_rank')
            ranks[player.id] = player.finish_rank
        if getattr(match, 'forfeit_reason', None) is not None:
            match.forfeit_reason = None
            match.forfeit_note = None
            await save_fields(match, 'forfeit_reason', 'forfeit_note')

        await self.audit_service.write_and_publish(  # type: ignore[attr-defined]
            actor,
            AuditActions.MATCH_RESULT_RECORDED,
            {
                'match_id': match.id,
                'winner_id': winner_id,
                'ranks': {str(k): v for k, v in ranks.items()},
            },
            EventType.MATCH_RESULT_RECORDED,
            event_extra={'tournament_id': match.tournament_id},
        )
        if first_result and announce:
            discord_queue.enqueue(result_alerts.match_result_in(match.id, actor.id))

        match_live.publish(match.id)

        return match
